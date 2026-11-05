/** 会话管理接口。所有请求都由 client.ts 自动带上 token。 */

import type { Conversation, ConversationDetail } from '../types'
import { api } from './client'

/** 侧边栏列表。后端只返回最近 30 天有更新的会话 */
export function listConversations(): Promise<Conversation[]> {
  return api.get<Conversation[]>('/conversations')
}

export function createConversation(title?: string): Promise<Conversation> {
  return api.post<Conversation>('/conversations', title ? { title } : {})
}

/** 进入会话时拉取完整消息 */
export function getConversation(id: string): Promise<ConversationDetail> {
  return api.get<ConversationDetail>(`/conversations/${id}`)
}

export function renameConversation(id: string, title: string): Promise<Conversation> {
  return api.patch<Conversation>(`/conversations/${id}`, { title })
}

/** 软删除：后端只打 deleted_at 标记，数据保留可恢复 */
export function deleteConversation(id: string): Promise<void> {
  return api.delete<void>(`/conversations/${id}`)
}

/** 清空消息但保留会话 */
export function clearConversationMessages(id: string): Promise<void> {
  return api.delete<void>(`/conversations/${id}/messages`)
}
