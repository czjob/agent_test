/**
 * 知识库接口。
 *
 * 只有四条：列表、上传、删除、重新索引。
 * 没有"编辑正文"——文档是用户上传的原始资料，可编辑就意味着要处理
 * "改了正文但向量还是旧的"这类一致性问题，收益远小于成本。
 * 想换内容就删了重传。
 */

import type { KnowledgeDocument } from '../types'
import { api, upload } from './client'

export function listDocuments(): Promise<KnowledgeDocument[]> {
  return api.get<KnowledgeDocument[]>('/documents')
}

/**
 * 上传一份文档。
 *
 * 返回值里的 status 一定是 pending：后端只做了解析和落库，
 * 切分和向量化在后台跑。要等它变 ready 才能检索到，所以调用方需要轮询。
 */
export function uploadDocument(file: File): Promise<KnowledgeDocument> {
  const form = new FormData()
  form.append('file', file)
  return upload<KnowledgeDocument>('/documents', form)
}

export function deleteDocument(id: string): Promise<void> {
  return api.delete<void>(`/documents/${id}`)
}

/** 重新切分并向量化。换了 embedding 模型或用久了想重跑时用 */
export function reindexDocument(id: string): Promise<{ id: string; status: string }> {
  return api.post<{ id: string; status: string }>(`/documents/${id}/reindex`)
}
