/**
 * 会话列表与当前会话的管理。
 *
 * 同样用模块作用域做单例：侧边栏、聊天窗口、App 各自调用 useConversations()
 * 拿到的是同一份列表和同一个 currentId，不需要层层传 props。
 */

import { ref } from 'vue'

import {
  clearConversationMessages,
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
} from '../api/conversations'
import type { Conversation } from '../types'

/** 记住上次打开的会话，刷新页面能回到原处 */
const CURRENT_KEY = 'agent_demo_current_conversation'

const items = ref<Conversation[]>([])
const currentId = ref<string | null>(localStorage.getItem(CURRENT_KEY))
const loading = ref(false)

function persistCurrent(id: string | null): void {
  currentId.value = id
  if (id) localStorage.setItem(CURRENT_KEY, id)
  else localStorage.removeItem(CURRENT_KEY)
}

export function useConversations() {
  /** 重新拉取列表（侧边栏的主要数据源） */
  async function refresh(): Promise<void> {
    loading.value = true
    try {
      items.value = await listConversations()
      // 当前会话可能已被清理（超过 30 天归档，或别的标签页删掉了），兜底切走
      if (currentId.value && !items.value.some((item) => item.id === currentId.value)) {
        persistCurrent(items.value[0]?.id ?? null)
      }
    } finally {
      loading.value = false
    }
  }

  async function create(): Promise<Conversation> {
    const conversation = await createConversation()
    items.value = [conversation, ...items.value]
    persistCurrent(conversation.id)
    return conversation
  }

  function select(id: string): void {
    persistCurrent(id)
  }

  async function rename(id: string, title: string): Promise<void> {
    const updated = await renameConversation(id, title)
    items.value = items.value.map((item) => (item.id === id ? updated : item))
  }

  async function remove(id: string): Promise<void> {
    await deleteConversation(id)
    items.value = items.value.filter((item) => item.id !== id)
    if (currentId.value === id) {
      persistCurrent(items.value[0]?.id ?? null)
    }
  }

  async function clearMessages(id: string): Promise<void> {
    await clearConversationMessages(id)
    items.value = items.value.map((item) =>
      item.id === id ? { ...item, message_count: 0, title: '新会话' } : item
    )
  }

  /** 退出登录时清空，避免下一个用户看到上一个用户的数据 */
  function reset(): void {
    items.value = []
    persistCurrent(null)
  }

  return {
    items,
    currentId,
    loading,
    refresh,
    create,
    select,
    rename,
    remove,
    clearMessages,
    reset,
  }
}
