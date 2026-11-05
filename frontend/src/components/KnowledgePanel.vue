<script setup lang="ts">
/**
 * 知识库面板（右侧抽屉）。
 *
 * 和记忆面板同样的定位：RAG 如果是黑盒，用户不知道 AI 到底能不能看到自己的资料，
 * 上传完没反应就只能反复刷新。所以这里必须把"处理到哪一步了"摆出来。
 *
 * 组件不碰网络：上传、删除、重新索引都由 App 通过 useDocuments 发起，
 * 这里只负责展示和转发事件。
 */
import { computed, ref } from 'vue'

import type { DocumentStatus, KnowledgeDocument } from '../types'
import { formatDateTime } from '../utils/date'

const props = defineProps<{
  open: boolean
  documents: KnowledgeDocument[]
  loading: boolean
  uploading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'upload', file: File): void
  (e: 'remove', id: string): void
  (e: 'reindex', id: string): void
}>()

const fileInput = ref<HTMLInputElement>()
const dragging = ref(false)

/** 后端支持的扩展名，和 knowledge.SUPPORTED_EXTS 保持一致 */
const ACCEPT = '.txt,.md,.markdown,.pdf'

const STATUS_META: Record<DocumentStatus, { label: string; color: string }> = {
  pending: { label: '排队中', color: '#8e8e93' },
  processing: { label: '处理中', color: '#007aff' },
  ready: { label: '可检索', color: '#34c759' },
  failed: { label: '失败', color: '#e5484d' },
}

const readyCount = computed(
  () => props.documents.filter((item) => item.status === 'ready').length
)

function pickFile(): void {
  fileInput.value?.click()
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('upload', file)
  // 清空 value：否则连续选同一个文件不会触发 change 事件
  input.value = ''
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) emit('upload', file)
}

/** 字节转人话。列表里写 1048576 没人看得懂 */
function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="overlay" @click="emit('close')" />
    </Transition>

    <Transition name="slide">
      <aside v-if="open" class="panel">
        <header class="head">
          <div>
            <h2>知识库</h2>
            <p class="sub">
              上传的资料会被切分并向量化，提问时 AI 会自己去检索原文
            </p>
          </div>
          <button type="button" class="close" title="关闭" @click="emit('close')">✕</button>
        </header>

        <div class="body">
          <!-- 上传区：点击和拖拽都能用 -->
          <div
            class="dropzone"
            :class="{ dragging, busy: uploading }"
            @click="pickFile"
            @dragover.prevent="dragging = true"
            @dragleave.prevent="dragging = false"
            @drop.prevent="onDrop"
          >
            <input
              ref="fileInput"
              type="file"
              :accept="ACCEPT"
              hidden
              @change="onFileChange"
            />
            <p class="dz-icon">{{ uploading ? '⏳' : '📄' }}</p>
            <p class="dz-main">{{ uploading ? '上传中…' : '点击选择，或把文件拖到这里' }}</p>
            <p class="dz-tip">支持 txt / md / pdf，单个文件不超过 5MB</p>
          </div>

          <p v-if="loading && documents.length === 0" class="hint">加载中…</p>

          <div v-else-if="documents.length === 0" class="empty">
            <p>还没有上传任何资料</p>
            <p class="tip">
              传一份文档试试，然后问「这份文件讲了什么」<br />
              AI 会先检索原文再回答，而不是凭空编
            </p>
          </div>

          <template v-else>
            <p class="list-label">
              共 {{ documents.length }} 份，可检索 {{ readyCount }} 份
            </p>

            <article v-for="doc in documents" :key="doc.id" class="item">
              <div class="item-main">
                <p class="filename" :title="doc.filename">{{ doc.filename }}</p>

                <div class="meta">
                  <span class="badge" :style="{ color: STATUS_META[doc.status].color }">
                    <span
                      class="dot"
                      :style="{ background: STATUS_META[doc.status].color }"
                      :class="{ pulse: doc.status === 'processing' || doc.status === 'pending' }"
                    />
                    {{ STATUS_META[doc.status].label }}
                  </span>
                  <span class="sep">·</span>
                  <span>{{ humanSize(doc.size_bytes) }}</span>
                  <template v-if="doc.status === 'ready'">
                    <span class="sep">·</span>
                    <span>{{ doc.chunk_count }} 段</span>
                  </template>
                </div>

                <!-- 失败原因直接展示：让用户知道该换文件还是该找运维 -->
                <p v-if="doc.status === 'failed' && doc.error" class="error">
                  {{ doc.error }}
                </p>

                <p class="time">{{ formatDateTime(doc.created_at) }}</p>
              </div>

              <div class="ops">
                <button
                  v-if="doc.status === 'failed'"
                  type="button"
                  class="op"
                  title="重新处理"
                  @click="emit('reindex', doc.id)"
                >
                  ↻
                </button>
                <button
                  type="button"
                  class="op danger"
                  title="删除"
                  @click="emit('remove', doc.id)"
                >
                  ✕
                </button>
              </div>
            </article>
          </template>
        </div>
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
  width: 400px;
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
  padding: 16px 18px 18px;
}

.dropzone {
  border: 1px dashed #dcdcdc;
  border-radius: 10px;
  padding: 22px 14px;
  text-align: center;
  cursor: pointer;
  transition: all 0.18s;
  background: #fcfcfc;
}

.dropzone:hover,
.dropzone.dragging {
  border-color: #9bbcf5;
  background: #f5f9ff;
}

.dropzone.busy {
  cursor: progress;
  opacity: 0.7;
}

.dz-icon {
  font-size: 22px;
  margin-bottom: 8px;
}

.dz-main {
  font-size: 13px;
  color: #444;
}

.dz-tip {
  margin-top: 6px;
  font-size: 11px;
  color: #aaa;
}

.list-label {
  margin: 18px 0 8px;
  font-size: 11px;
  color: #999;
}

.item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 11px 10px 11px 12px;
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

.filename {
  font-size: 13px;
  color: #333;
  line-height: 1.5;
  /* 长文件名只留一行，超出省略——两行会把列表撑得很松散 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 6px;
  font-size: 11px;
  color: #999;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

/* 处理中的点做个呼吸，让"它真的在动"这件事有视觉证据 */
.pulse {
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

.sep {
  color: #dcdcdc;
}

.error {
  margin-top: 6px;
  font-size: 11px;
  color: #e5484d;
  line-height: 1.6;
  word-break: break-word;
}

.time {
  margin-top: 6px;
  font-size: 11px;
  color: #c4c4c4;
}

.ops {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex-shrink: 0;
}

.op {
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: #ccc;
  font-size: 12px;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.6;
  transition: all 0.15s;
}

.item:hover .op {
  opacity: 1;
}

.op:hover {
  background: #f2f2f2;
  color: #666;
}

.op.danger:hover {
  background: #fdecec;
  color: #e5484d;
}

.hint {
  font-size: 13px;
  color: #bbb;
  text-align: center;
  padding-top: 40px;
}

.empty {
  text-align: center;
  color: #666;
  font-size: 13px;
  line-height: 1.9;
  padding-top: 40px;
}

.empty .tip {
  margin-top: 8px;
  font-size: 12px;
  color: #aaa;
  line-height: 1.9;
}

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
