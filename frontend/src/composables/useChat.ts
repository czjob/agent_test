/**
 * 对话状态与流式发送。
 *
 * 这个 composable 负责：把数据库里的消息行转成界面能用的一维列表、
 * 管理"正在发送"状态、以及把 SSE 事件增量写进当前气泡。
 * 真正的网络与协议解析在 api/chat.ts，这里只管状态。
 */

import { ref } from 'vue'

import { streamChat } from '../api/chat'
import { getConversation } from '../api/conversations'
import type { MessageRow, UiMessage } from '../types'

/**
 * 把后端消息行转换成界面消息。
 *
 * 关键点：一次工具调用在库里是两条记录（assistant 带 tool_calls + tool 带结果），
 * 但界面上应该是同一个气泡——否则每次调工具都会多出一个空气泡。
 * 所以这里做两件合并：
 *   1. tool 行的结果追加到上一条 assistant 的 toolLines
 *   2. 紧随其后的 assistant 正文，合并进那条"只有工具调用、没有正文"的气泡
 */
function toUiMessages(rows: MessageRow[]): UiMessage[] {
  const result: UiMessage[] = []

  for (const row of rows) {
    if (row.role === 'user') {
      result.push({ role: 'user', content: row.content, toolLines: [] })
      continue
    }

    if (row.role === 'tool') {
      const last = result[result.length - 1]
      const line = `✅ 工具返回：${row.content}`
      if (last && last.role === 'assistant') last.toolLines.push(line)
      else result.push({ role: 'assistant', content: '', toolLines: [line] })
      continue
    }

    // assistant
    const last = result[result.length - 1]
    if (last && last.role === 'assistant' && !last.content) {
      // 上一条正是"只发了工具调用"的占位气泡，正文补进去即可
      last.content = row.content
      continue
    }
    result.push({ role: 'assistant', content: row.content, toolLines: [] })
  }

  return result
}

interface UseChatOptions {
  /** 每轮对话结束后的回调，App 用它刷新侧边栏的标题和排序 */
  onFinished?: () => void
}

export function useChat(options: UseChatOptions = {}) {
  const messages = ref<UiMessage[]>([])
  const sending = ref(false)

  /** 用来中途取消请求（切会话 / 关页面时） */
  let controller: AbortController | null = null

  async function load(conversationId: string): Promise<void> {
    const detail = await getConversation(conversationId)
    messages.value = toUiMessages(detail.messages)
  }

  function clear(): void {
    messages.value = []
  }

  function stop(): void {
    controller?.abort()
  }

  async function send(conversationId: string, text: string): Promise<void> {
    if (sending.value) return

    // 先把用户消息和一个空的助手气泡推上去，界面立刻有反馈
    messages.value.push({ role: 'user', content: text, toolLines: [] })
    messages.value.push({ role: 'assistant', content: '', toolLines: [], streaming: true })

    // 【Vue 响应式关键点】必须从响应式数组里"读出来"再改：
    // 直接改 push 进去的原始对象不会触发重渲染，表现为"整段回答一次性出现"。
    const bubble = messages.value[messages.value.length - 1]

    sending.value = true
    controller = new AbortController()

    try {
      await streamChat(
        conversationId,
        text,
        {
          onToken: (content) => {
            bubble.content += content
          },
          onToolStart: (content) => {
            bubble.toolLines.push(content)
          },
          onToolEnd: (content) => {
            bubble.toolLines.push(content)
          },
          onError: (content) => {
            bubble.content += `${bubble.content ? '\n' : ''}❌ ${content}`
            bubble.failed = true
          },
          onDone: () => {
            // 结束标记，收尾统一放在 finally 里
          },
        },
        controller.signal
      )
    } catch (error) {
      // 用户主动取消（切会话）不算错误，静默处理
      if (error instanceof DOMException && error.name === 'AbortError') return

      bubble.failed = true
      const message = error instanceof Error ? error.message : String(error)
      bubble.content += `${bubble.content ? '\n' : ''}❌ ${message}`
    } finally {
      bubble.streaming = false
      sending.value = false
      controller = null
      options.onFinished?.()
    }
  }

  return { messages, sending, load, send, stop, clear }
}
