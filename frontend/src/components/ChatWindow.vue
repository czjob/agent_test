<script setup lang="ts">
/**
 * 主会话窗口：顶部标题栏 + 可滚动的消息区。
 *
 * 输入框单独做成 ChatInput，是因为它有自己的焦点/禁用/回车逻辑，
 * 和"展示消息"是两件不同的事。
 */
import { nextTick, ref, watch } from 'vue'

import type { UiMessage } from '../types'
import MessageBubble from './MessageBubble.vue'

const props = defineProps<{
  title: string
  messages: UiMessage[]
  /** 是否选中了某个会话。没有会话时显示空状态 */
  hasConversation: boolean
  /** 正在从后端拉取历史消息 */
  loading: boolean
  /** 当前用户已积累的长期记忆条数，显示在按钮角标上 */
  memoryCount: number
}>()

const emit = defineEmits<{
  (e: 'clear'): void
  (e: 'open-memories'): void
}>()

const scrollRef = ref<HTMLElement>()

/** 用户手动往上翻看历史时不要再强制拉到底，否则很烦 */
function isNearBottom(): boolean {
  const el = scrollRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

function scrollToBottom(force = false): void {
  const shouldScroll = force || isNearBottom()
  // nextTick：等 Vue 把新 DOM 渲染完再设置滚动位置，否则算出来的 scrollHeight 是旧的
  nextTick(() => {
    const el = scrollRef.value
    if (el && shouldScroll) el.scrollTop = el.scrollHeight
  })
}

// 监听"最后一条消息的内容长度 + 工具行数量"：
// 比 deep watch 整个数组便宜，又能在每个 token 到达时触发滚动
watch(
  () => {
    const last = props.messages[props.messages.length - 1]
    return last ? last.content.length + last.toolLines.length : 0
  },
  () => scrollToBottom()
)

// 切换会话（消息整体替换）时强制回到底部
watch(
  () => props.messages.length === 0,
  () => scrollToBottom(true)
)
watch(
  () => props.hasConversation,
  () => scrollToBottom(true)
)
</script>

<template>
  <section class="chat-window">
    <header class="header">
      <h1>{{ hasConversation ? title : 'LangChain Agent' }}</h1>

      <div class="actions">
        <!-- 记忆按钮常驻：它是全局的，不依赖当前有没有会话 -->
        <button type="button" class="mem-btn" title="查看 AI 记住了什么" @click="emit('open-memories')">
          🧠 记忆
          <span v-if="memoryCount > 0" class="badge">{{ memoryCount }}</span>
        </button>

        <button
          v-if="hasConversation && messages.length > 0"
          type="button"
          class="clear-btn"
          @click="emit('clear')"
        >
          清空对话
        </button>
      </div>
    </header>

    <div ref="scrollRef" class="scroll">
      <p v-if="loading" class="hint">加载中…</p>

      <div v-else-if="!hasConversation" class="empty">
        <p class="emoji">💬</p>
        <p>新建一个会话开始聊天吧</p>
        <p class="tip">试试问：现在几点？或者帮我算一下 1024 * 8</p>
      </div>

      <div v-else-if="messages.length === 0" class="empty">
        <p class="emoji">👋</p>
        <p>你好，我是你的 AI 助手</p>
        <p class="tip">试试问：现在几点？或者帮我算一下 1024 * 8</p>
      </div>

      <MessageBubble v-for="(message, index) in messages" :key="index" :message="message" />
    </div>
  </section>
</template>

<style scoped>
.chat-window {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0; /* 关键：让内部 overflow 生效，否则 flex 子项会被内容撑高 */
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 22px;
  border-bottom: 1px solid #f0f0f0;
  background: #fff;
}

h1 {
  font-size: 15px;
  font-weight: 500;
  color: #222;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.mem-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 11px;
  font-size: 12px;
  color: #666;
  background: #fff;
  border: 1px solid #e4e4e4;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.mem-btn:hover {
  color: #007aff;
  border-color: #b9d9ff;
  background: #f7fbff;
}

/* 有记忆时才出现的角标，条数本身就是"它在记我"的直觉反馈 */
.badge {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #007aff;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}

.clear-btn {
  flex-shrink: 0;
  padding: 5px 12px;
  font-size: 12px;
  color: #888;
  background: #fff;
  border: 1px solid #e4e4e4;
  border-radius: 6px;
  cursor: pointer;
}

.clear-btn:hover {
  color: #e5484d;
  border-color: #f0c4c4;
}

.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 20px 22px;
}

.hint {
  text-align: center;
  font-size: 13px;
  color: #bbb;
  padding-top: 80px;
}

.empty {
  text-align: center;
  color: #666;
  padding-top: 90px;
  font-size: 14px;
  line-height: 2;
}

.empty .emoji {
  font-size: 28px;
}

.empty .tip {
  font-size: 13px;
  color: #aaa;
}
</style>
