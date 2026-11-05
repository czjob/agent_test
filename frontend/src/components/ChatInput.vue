<script setup lang="ts">
/**
 * 输入区。
 *
 * 只在"真正发出去了"之后才清空输入框——这样网络失败时用户不会白丢一段话。
 * 输入过程中 Enter 发送、Shift+Enter 换行，是聊天框的通用习惯。
 */
import { computed, ref } from 'vue'

const props = defineProps<{
  disabled: boolean
  placeholder: string
}>()

const emit = defineEmits<{ (e: 'send', text: string): void }>()

const text = ref('')
const textareaRef = ref<HTMLTextAreaElement>()

const canSend = computed(() => !props.disabled && text.value.trim().length > 0)

function submit(): void {
  if (!canSend.value) return
  const content = text.value.trim()
  text.value = ''
  emit('send', content)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault() // 阻止 textarea 默认的换行
    submit()
  }
}

function focus(): void {
  textareaRef.value?.focus()
}

// 让父组件能在切换会话后把焦点放回输入框
defineExpose({ focus })
</script>

<template>
  <div class="input-area">
    <textarea
      ref="textareaRef"
      v-model="text"
      rows="2"
      :placeholder="placeholder"
      :disabled="disabled"
      @keydown="onKeydown"
    />
    <button type="button" :disabled="!canSend" @click="submit">
      {{ disabled ? '回复中…' : '发送' }}
    </button>
  </div>
</template>

<style scoped>
.input-area {
  display: flex;
  gap: 10px;
  padding: 14px 22px 18px;
  border-top: 1px solid #f0f0f0;
  background: #fff;
}

textarea {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid #e0e0e0;
  border-radius: 10px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.6;
  resize: none;
  outline: none;
  transition: border-color 0.15s;
}

textarea:focus {
  border-color: #007aff;
}

textarea:disabled {
  background: #fafafa;
  color: #aaa;
}

button {
  padding: 0 24px;
  border: none;
  border-radius: 10px;
  background: #007aff;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s;
}

button:disabled {
  background: #c9c9c9;
  cursor: not-allowed;
}
</style>
