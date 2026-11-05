/**
 * 长期记忆接口。
 *
 * 只有三条：列表、删一条、清空全部。
 * 没有"新增"——记忆由后端在每轮对话结束后自动抽取，
 * 前端不该有手动写入的入口，否则会出现"用户编造的记忆被当成事实注入"。
 */

import type { Memory } from '../types'
import { api } from './client'

export function listMemories(): Promise<Memory[]> {
  return api.get<Memory[]>('/memories')
}

/** 删单条。后端是软删除：打标记而不是真删，避免下一轮又被抽回来 */
export function deleteMemory(id: number): Promise<void> {
  return api.delete<void>(`/memories/${id}`)
}

/** 清空全部，返回清掉的条数（面板上要给一句"已清除 N 条"的反馈） */
export function clearMemories(): Promise<{ deleted: number }> {
  return api.delete<{ deleted: number }>('/memories')
}
