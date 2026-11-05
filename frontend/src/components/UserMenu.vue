<script setup lang="ts">
/** 侧边栏底部的用户区：头像 + 名字，点开可退出登录。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { User } from '../types'

const props = defineProps<{ user: User }>()
const emit = defineEmits<{ (e: 'logout'): void }>()

const open = ref(false)
const rootRef = ref<HTMLElement>()

/** 头像显示名字首字。中文取第一个字，英文取首字母大写 */
const initial = computed(() => props.user.display_name.charAt(0).toUpperCase())

function toggle(): void {
  open.value = !open.value
}

/** 点击外部关闭。用 document 监听是这种小型弹层的常规做法 */
function onDocumentClick(event: MouseEvent): void {
  if (!open.value) return
  if (rootRef.value && !rootRef.value.contains(event.target as Node)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div ref="rootRef" class="user-menu">
    <button type="button" class="trigger" @click="toggle">
      <span class="avatar">{{ initial }}</span>
      <span class="info">
        <span class="name">{{ user.display_name }}</span>
        <span class="account">@{{ user.username }}</span>
      </span>
      <span class="caret">▾</span>
    </button>

    <div v-if="open" class="popover">
      <button type="button" class="logout" @click="emit('logout')">退出登录</button>
    </div>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
}

.trigger {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.trigger:hover {
  background: rgba(0, 0, 0, 0.04);
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: #007aff;
  color: #fff;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.name {
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.account {
  font-size: 11px;
  color: #aaa;
}

.caret {
  font-size: 10px;
  color: #bbb;
}

.popover {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  right: 0;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.1);
  padding: 4px;
  z-index: 10;
}

.logout {
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 13px;
  color: #e5484d;
  text-align: left;
  cursor: pointer;
}

.logout:hover {
  background: #fdecec;
}
</style>
