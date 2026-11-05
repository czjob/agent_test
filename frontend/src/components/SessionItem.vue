<script setup lang="ts">
/**
 * 侧边栏的单个会话项。
 *
 * 重命名的编辑态放在组件内部（local state），因为它只影响自己这一行；
 * 真正的保存动作 emit 给上层统一调接口。
 */
import { nextTick, ref } from 'vue'

import type { Conversation } from '../types'
import { formatShortTime } from '../utils/date'

const props = defineProps<{
  conversation: Conversation
  active: boolean
}>()

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'rename', id: string, title: string): void
  (e: 'remove', id: string): void
}>()

const editing = ref(false)
const draft = ref('')
const inputRef = ref<HTMLInputElement>()

async function startEdit(): Promise<void> {
  draft.value = props.conversation.title
  editing.value = true
  await nextTick()
  inputRef.value?.focus()
  inputRef.value?.select()
}

function commit(): void {
  if (!editing.value) return
  const title = draft.value.trim()
  editing.value = false
  // 没改动或改成空就直接放弃，避免发无意义的请求
  if (!title || title === props.conversation.title) return
  emit('rename', props.conversation.id, title)
}

function cancel(): void {
  editing.value = false
}
</script>

<template>
  <div class="session-item" :class="{ active }" @click="emit('select', conversation.id)">
    <input
      v-if="editing"
      ref="inputRef"
      v-model="draft"
      class="rename-input"
      @click.stop
      @keydown.enter.prevent="commit"
      @keydown.esc.prevent="cancel"
      @blur="commit"
    />

    <template v-else>
      <div class="body">
        <div class="title">{{ conversation.title }}</div>
        <div class="meta">
          {{ formatShortTime(conversation.updated_at) }}
          <template v-if="conversation.message_count"> · {{ conversation.message_count }} 条</template>
        </div>
      </div>
      <div class="actions">
        <button type="button" title="重命名" @click.stop="startEdit">✎</button>
        <button type="button" title="删除" @click.stop="emit('remove', conversation.id)">✕</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.session-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s;
}

.session-item:hover {
  background: rgba(0, 0, 0, 0.04);
}

.session-item.active {
  background: #e8f1fd;
}

.body {
  flex: 1;
  min-width: 0; /* 关键：让内部文字能省略号截断，否则 flex 子项不会收缩 */
}

.title {
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-item.active .title {
  color: #007aff;
  font-weight: 500;
}

.meta {
  font-size: 11px;
  color: #aaa;
  margin-top: 2px;
}

.actions {
  display: none;
  gap: 2px;
  flex-shrink: 0;
}

.session-item:hover .actions {
  display: flex;
}

.actions button {
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: #999;
  font-size: 12px;
  cursor: pointer;
  line-height: 1;
}

.actions button:hover {
  background: rgba(0, 0, 0, 0.08);
  color: #333;
}

.rename-input {
  width: 100%;
  padding: 5px 8px;
  border: 1px solid #007aff;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}
</style>
