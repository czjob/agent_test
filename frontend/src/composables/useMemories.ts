/**
 * 长期记忆的状态管理。
 *
 * 和 useConversations 一样用模块作用域做单例：
 * 记忆面板、头部按钮（显示条数）各自调用 useMemories()，
 * 拿到的是同一份数据，删一条两边同时更新。
 *
 * 刷新时机有三个，都刻意不做自动轮询：
 *   1. 登录后（bootstrap）—— 让角标一开始就有数
 *   2. 每轮对话结束后延迟 3 秒 —— 抽取是后台任务，接口返回 done 时它还没写完
 *   3. 打开面板时 —— 用户点开就想看到最新的，不能是几秒前的缓存
 */

import { ref } from 'vue'

import { clearMemories, deleteMemory, listMemories } from '../api/memories'
import type { Memory } from '../types'

const items = ref<Memory[]>([])
const loading = ref(false)

export function useMemories() {
  async function refresh(): Promise<void> {
    loading.value = true
    try {
      items.value = await listMemories()
    } finally {
      loading.value = false
    }
  }

  async function remove(id: number): Promise<void> {
    await deleteMemory(id)
    // 本地直接摘掉，不重新拉列表：
    // 删一条就整表重拉会让面板闪一下，而且结果完全可预测
    items.value = items.value.filter((item) => item.id !== id)
  }

  async function clearAll(): Promise<number> {
    const { deleted } = await clearMemories()
    items.value = []
    return deleted
  }

  /** 退出登录 / 切换用户时清空，避免看到上一个用户的记忆 */
  function reset(): void {
    items.value = []
  }

  return { items, loading, refresh, remove, clearAll, reset }
}
