/**
 * 知识库的状态管理。
 *
 * 和 useMemories 一样用模块作用域做单例：面板和头部角标共享同一份数据。
 *
 * 为什么是轮询而不是 WebSocket / SSE：
 *   一份文档的向量化通常 5~30 秒结束，状态只在这段时间里变化。
 *   为了这点需求再拉一条长连接，要额外处理重连、心跳、多标签页同步，
 *   收益完全不成正比。
 *
 * 但轮询也不是无脑定时拉：只有存在 pending / processing 的文档时才启动，
 * 全部就绪（或失败）立刻停掉。空闲状态下这个页面一个请求都不发。
 */

import { ref } from 'vue'

import {
  deleteDocument,
  listDocuments,
  reindexDocument,
  uploadDocument,
} from '../api/documents'
import type { KnowledgeDocument } from '../types'

/** 轮询间隔。太短会给后端制造无意义压力，太长用户会觉得卡 */
const POLL_MS = 2000

const items = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const uploading = ref(false)

let pollTimer: number | undefined

function hasPending(list: KnowledgeDocument[]): boolean {
  return list.some((item) => item.status === 'pending' || item.status === 'processing')
}

function stopPoll(): void {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function startPoll(): void {
  if (pollTimer !== undefined) return
  pollTimer = window.setInterval(() => {
    void tick()
  }, POLL_MS)
}

async function tick(): Promise<void> {
  try {
    await refresh()
  } catch {
    // 网络抖动 / 登录过期时别再继续打：停掉轮询，下次操作会重新拉起
    stopPoll()
    return
  }
  if (!hasPending(items.value)) stopPoll()
}

/** 已就绪的文档数，用于头部角标 */
function readyCount(): number {
  return items.value.filter((item) => item.status === 'ready').length
}

async function refresh(): Promise<void> {
  loading.value = true
  try {
    items.value = await listDocuments()
  } finally {
    loading.value = false
  }
  // 刷新（比如打开面板）时如果发现有正在处理的，把它接上进度
  if (hasPending(items.value)) startPoll()
}

async function upload(file: File): Promise<KnowledgeDocument> {
  uploading.value = true
  try {
    const doc = await uploadDocument(file)
    // 先塞进列表，状态是 pending，用户立刻能看见"已收到，正在处理"
    items.value = [doc, ...items.value]
    startPoll()
    return doc
  } finally {
    uploading.value = false
  }
}

async function remove(id: string): Promise<void> {
  await deleteDocument(id)
  // 本地摘掉而不是整表重拉：结果完全可预测，也不会让面板闪一下
  items.value = items.value.filter((item) => item.id !== id)
  if (!hasPending(items.value)) stopPoll()
}

async function reindex(id: string): Promise<void> {
  await reindexDocument(id)
  items.value = items.value.map((item) =>
    item.id === id ? { ...item, status: 'pending', error: null, chunk_count: 0 } : item
  )
  startPoll()
}

/** 退出登录 / 切换用户时清空，避免看到上一个用户的文档 */
function reset(): void {
  stopPoll()
  items.value = []
}

export function useDocuments() {
  return { items, loading, uploading, readyCount, refresh, upload, remove, reindex, reset }
}
