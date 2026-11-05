<script setup lang="ts">
/**
 * 记忆面板（右侧抽屉）。
 *
 * 为什么必须有这个面板：
 *   记忆是"后端主动记下来的东西"。如果用户看不到，它就变成了黑盒——
 *   回答突然提到"你在杭州做前端"，用户会觉得模型在乱猜，而不是"它记得我"。
 *   能看见 + 能删掉，是记忆功能可被信任的前提。
 *
 * 组件本身不碰网络：拉取和删除由 App 通过 useMemories 统一发起，
 * 这里只负责展示和转发事件，和 SessionSidebar 的职责划分保持一致。
 */
import { computed } from 'vue'

import type { Memory, MemoryKind } from '../types'
import { formatDateTime } from '../utils/date'

const props = defineProps<{
  open: boolean
  memories: Memory[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'remove', id: number): void
  (e: 'clear'): void
}>()

/** 三类记忆的展示顺序和文案。顺序即"从最稳定到最具体" */
const KIND_META: Record<MemoryKind, { label: string; color: string }> = {
  profile: { label: '身份信息', color: '#007aff' },
  preference: { label: '偏好与要求', color: '#34c759' },
  fact: { label: '背景事实', color: '#af52de' },
}

const KIND_ORDER: MemoryKind[] = ['profile', 'preference', 'fact']

/** 分组展示，空组不渲染 */
const groups = computed(() =>
  KIND_ORDER.map((kind) => ({
    kind,
    meta: KIND_META[kind],
    items: props.memories.filter((item) => item.kind === kind),
  })).filter((group) => group.items.length > 0)
)
</script>

<template>
  <!-- Teleport 到 body：抽屉要盖住整个视口，
       留在 App 的 flex 容器里会被 overflow 和 z-index 上下文困住 -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="overlay" @click="emit('close')" />
    </Transition>

    <Transition name="slide">
      <aside v-if="open" class="panel">
        <header class="head">
          <div>
            <h2>AI 记住了什么</h2>
            <p class="sub">这些是从你的对话里自动提取的，会注入到每一轮对话中</p>
          </div>
          <button type="button" class="close" title="关闭" @click="emit('close')">✕</button>
        </header>

        <div class="body">
          <p v-if="loading && memories.length === 0" class="hint">加载中…</p>

          <div v-else-if="memories.length === 0" class="empty">
            <p class="emoji">🧠</p>
            <p>还没有记住任何东西</p>
            <p class="tip">
              试着在对话里说一下自己的身份或偏好，<br />
              比如「我是前端工程师，回答尽量简洁」
            </p>
          </div>

          <template v-else>
            <section v-for="group in groups" :key="group.kind" class="group">
              <p class="group-label">
                <span class="dot" :style="{ background: group.meta.color }" />
                {{ group.meta.label }}
                <span class="count">{{ group.items.length }}</span>
              </p>

              <article v-for="item in group.items" :key="item.id" class="item">
                <div class="item-main">
                  <p class="content">{{ item.content }}</p>
                  <div class="meta">
                    <!-- 重要度用细条表示：数字对用户没什么意义，长度一眼可比 -->
                    <span class="bar" :title="`重要度 ${Math.round(item.importance * 100)}%`">
                      <span
                        class="bar-fill"
                        :style="{
                          width: `${Math.round(item.importance * 100)}%`,
                          background: group.meta.color,
                        }"
                      />
                    </span>
                    <span class="time">
                      {{ item.last_used_at ? `最近使用 ${formatDateTime(item.last_used_at)}` : '尚未使用' }}
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  class="remove"
                  title="忘记这条"
                  @click="emit('remove', item.id)"
                >
                  ✕
                </button>
              </article>
            </section>
          </template>
        </div>

        <footer v-if="memories.length > 0" class="foot">
          <button type="button" class="clear-all" @click="emit('clear')">清空全部记忆</button>
        </footer>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.18);
  z-index: 40;
}

.panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 380px;
  max-width: 92vw;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-left: 1px solid #ececec;
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.06);
  z-index: 41;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 18px 14px;
  border-bottom: 1px solid #f0f0f0;
}

h2 {
  font-size: 15px;
  font-weight: 500;
  color: #222;
}

.sub {
  margin-top: 5px;
  font-size: 12px;
  color: #999;
  line-height: 1.6;
}

.close {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  color: #aaa;
  font-size: 14px;
  border-radius: 6px;
  cursor: pointer;
}

.close:hover {
  background: #f5f5f5;
  color: #555;
}

.body {
  flex: 1;
  overflow-y: auto;
  padding: 6px 18px 18px;
}

.group {
  margin-top: 16px;
}

.group-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #999;
  padding-bottom: 8px;
  user-select: none;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.count {
  color: #c4c4c4;
}

.item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 10px 10px 12px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  margin-bottom: 8px;
  transition: border-color 0.15s, background 0.15s;
}

.item:hover {
  border-color: #e0e0e0;
  background: #fcfcfc;
}

.item-main {
  flex: 1;
  min-width: 0;
}

.content {
  font-size: 13px;
  color: #333;
  line-height: 1.65;
  word-break: break-word;
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 7px;
}

.bar {
  width: 52px;
  height: 3px;
  border-radius: 2px;
  background: #f0f0f0;
  overflow: hidden;
  flex-shrink: 0;
}

.bar-fill {
  display: block;
  height: 100%;
  border-radius: 2px;
}

.time {
  font-size: 11px;
  color: #bbb;
}

/* 删除按钮常驻但很淡：移动端没有 hover，藏起来等于删不掉 */
.remove {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: #ccc;
  font-size: 11px;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.5;
  transition: all 0.15s;
}

.item:hover .remove,
.remove:focus-visible {
  opacity: 1;
}

.remove:hover {
  background: #fdecec;
  color: #e5484d;
}

.hint {
  font-size: 13px;
  color: #bbb;
  text-align: center;
  padding-top: 60px;
}

.empty {
  text-align: center;
  color: #666;
  font-size: 13px;
  line-height: 1.9;
  padding-top: 70px;
}

.empty .emoji {
  font-size: 26px;
  margin-bottom: 6px;
}

.empty .tip {
  margin-top: 10px;
  font-size: 12px;
  color: #aaa;
  line-height: 1.9;
}

.foot {
  border-top: 1px solid #f0f0f0;
  padding: 12px 18px;
}

.clear-all {
  width: 100%;
  padding: 9px;
  font-size: 12px;
  color: #888;
  background: #fff;
  border: 1px solid #e4e4e4;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.clear-all:hover {
  color: #e5484d;
  border-color: #f0c4c4;
}

/* 抽屉滑入 / 遮罩淡入 */
.slide-enter-active,
.slide-leave-active {
  transition: transform 0.22s ease;
}

.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.22s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
