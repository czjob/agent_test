/**
 * 流式对话接口（SSE）。
 *
 * 为什么不用 EventSource：
 *   EventSource 只支持 GET，而我们要 POST 一段 JSON（conversation_id + input）。
 *   所以用 fetch 拿 ReadableStream，自己按 SSE 协议切包——这也是浏览器原生的做法。
 *
 * 【最容易出错的地方】网络分包不保证按消息边界切
 *   一条 SSE 报文是 "data: {...}\n\n"，但 TCP 可能把半条 JSON 送到你手上。
 *   所以必须维护一个 buffer：每次追加新数据，按 \n\n 切出完整报文，
 *   最后一段（可能是半条）留到下次再拼。少了这一步，中文或长回答必现 JSON.parse 报错。
 */

import { ApiError, getToken } from './client'

export interface ChatStreamHandlers {
  onToken: (content: string) => void
  onToolStart: (content: string) => void
  onToolEnd: (content: string) => void
  onError: (content: string) => void
  onDone: () => void
}

interface SsePayload {
  type: 'token' | 'tool_start' | 'tool_end' | 'error' | 'done'
  content?: string
}

export async function streamChat(
  conversationId: string,
  input: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const token = getToken()
  const res = await fetch('/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ conversation_id: conversationId, input }),
    signal,
  })

  // HTTP 层面的失败（401 / 404 / 422）不会走 SSE，直接当普通错误处理
  if (!res.ok) {
    let detail = `请求失败（HTTP ${res.status}）`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // 响应体不是 JSON，用兜底文案
    }
    throw new ApiError(res.status, detail)
  }

  if (!res.body) throw new Error('响应体为空，无法读取流')

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    // stream: true 告诉解码器"可能还有后续字节"，避免多字节汉字被从中间截断
    buffer += decoder.decode(value, { stream: true })

    const chunks = buffer.split('\n\n')
    // pop 出来的是可能不完整的最后一段，留到下一轮
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      // SSE 还支持 event:/id:/retry: 等字段，本项目只用 data:
      if (!chunk.startsWith('data: ')) continue

      let payload: SsePayload
      try {
        payload = JSON.parse(chunk.slice(6)) as SsePayload
      } catch {
        // 单条报文解析失败不应该打断整条流，跳过即可
        continue
      }

      switch (payload.type) {
        case 'token':
          handlers.onToken(payload.content ?? '')
          break
        case 'tool_start':
          handlers.onToolStart(payload.content ?? '')
          break
        case 'tool_end':
          handlers.onToolEnd(payload.content ?? '')
          break
        case 'error':
          handlers.onError(payload.content ?? '未知错误')
          break
        case 'done':
          handlers.onDone()
          break
      }
    }
  }
}
