<script setup lang="ts">
/**
 * 左侧会话侧边栏。
 *
 * 职责只有"展示 + 转发事件"：分组由 utils/date.ts 计算，
 * 增删改的实际请求由 App 统一发起，组件本身不碰网络。
 */
import { computed } from 'vue'

import type { Conversation, User } from '../types'
import { groupConversations } from '../utils/date'
import SessionItem from './SessionItem.vue'
import UserMenu from './UserMenu.vue'

const props = defineProps<{
  conversations: Conversation[]
  currentId: string | null
  user: User
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'create'): void
  (e: 'select', id: string): void
  (e: 'rename', id: string, title: string): void
  (e: 'remove', id: string): void
  (e: 'logout'): void
}>()

/** 按「今天 / 昨天 / 本周 / 更早」分组，空组自动去掉 */
const groups = computed(() => groupConversations(props.conversations))
</script>

<template>
  <aside class="sidebar">
    <div class="top">
      <button type="button" class="new-btn" @click="emit('create')">
        <span class="plus">+</span> 新建会话
      </button>
    </div>

    <div class="list">
      <p v-if="loading && conversations.length === 0" class="hint">加载中…</p>
      <p v-else-if="conversations.length === 0" class="hint">
        还没有会话<br />点上方按钮开始第一个
      </p>

      <template v-for="group in groups" :key="group.label">
        <p class="group-label">{{ group.label }}</p>
        <SessionItem
          v-for="conversation in group.items"
          :key="conversation.id"
          :conversation="conversation"
          :active="conversation.id === currentId"
          @select="emit('select', $event)"
          @rename="(id, title) => emit('rename', id, title)"
          @remove="emit('remove', $event)"
        />
      </template>
    </div>

    <div class="bottom">
      <UserMenu :user="user" @logout="emit('logout')" />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 248px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fafafa;
  border-right: 1px solid #ececec;
}

.top {
  padding: 12px;
}

.new-btn {
  width: 100%;
  padding: 9px 12px;
  border: 1px solid #dcdcdc;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  color: #333;
  cursor: pointer;
  transition: all 0.15s;
}

.new-btn:hover {
  border-color: #007aff;
  color: #007aff;
}

.plus {
  font-size: 14px;
  margin-right: 2px;
}

.list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}

.group-label {
  font-size: 11px;
  color: #aaa;
  padding: 12px 10px 4px;
  user-select: none;
}

.hint {
  font-size: 12px;
  color: #bbb;
  text-align: center;
  padding: 32px 12px;
  line-height: 1.8;
}

.bottom {
  border-top: 1px solid #ececec;
  padding: 8px;
}
</style>
