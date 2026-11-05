<script setup lang="ts">
/**
 * 单条消息气泡。
 *
 * 独立成组件的原因：以后要加 Markdown 渲染、代码高亮、复制按钮、重新生成，
 * 都只改这一个文件，不会牵动聊天窗口的布局。
 */
import type { UiMessage } from '../types'

defineProps<{ message: UiMessage }>()
</script>

<template>
  <div class="message" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '🧑' : '🤖' }}</div>

    <div class="bubble" :class="{ failed: message.failed }">
      <!-- 工具调用状态区：本次回答过程中调了哪些工具、返回了什么 -->
      <div v-if="message.toolLines.length > 0" class="tool-info">
        <div v-for="(line, index) in message.toolLines" :key="index" class="tool-line">
          {{ line }}
        </div>
      </div>

      <div v-if="message.content" class="content">{{ message.content }}</div>

      <!-- 流式光标：只在这一条还在生成时闪烁 -->
      <span v-if="message.streaming" class="cursor">▋</span>

      <!-- 还没收到任何 token 也没有工具调用时的占位，避免出现空气泡 -->
      <span v-else-if="!message.content && message.toolLines.length === 0" class="pending">
        正在思考…
      </span>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.message.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #ececec;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.bubble {
  max-width: 72%;
  padding: 10px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #f0f0f0;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.message.user .bubble {
  background: #007aff;
  border-color: #007aff;
  color: #fff;
}

.bubble.failed {
  border-color: #f5c2c2;
  background: #fffafa;
}

.tool-info {
  font-size: 12px;
  color: #b07800;
  background: #fff8e6;
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 8px;
  line-height: 1.6;
}

.tool-line + .tool-line {
  margin-top: 2px;
}

.cursor {
  color: #007aff;
  animation: blink 1s infinite;
}

@keyframes blink {
  0%,
  50% {
    opacity: 1;
  }
  51%,
  100% {
    opacity: 0;
  }
}

.pending {
  color: #bbb;
  font-size: 13px;
}
</style>
