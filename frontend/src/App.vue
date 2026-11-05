<script setup lang="ts">
/**
 * 应用根组件：只做"装配"和"事件编排"。
 *
 * 状态分布：
 *   useAuth()          登录态（全局单例）
 *   useConversations() 会话列表 + 当前会话 id（全局单例）
 *   useMemories()      长期记忆列表（全局单例）
 *   useChat()          当前会话的消息与发送状态（本组件持有，跟着当前会话切换）
 *
 * 这里刻意不用 watch 监听 currentId 去加载消息，而是每个操作里显式调用加载。
 * 原因：watch 是异步的，"新建会话 → 立刻发消息"这种连贯操作下，
 * watch 触发的空会话加载可能晚于消息 push 回来，把刚推上去的气泡清掉。
 */

import { onMounted, onUnmounted, ref } from 'vue'

import { onUnauthorized } from './api/client'
import ChatInput from './components/ChatInput.vue'
import ChatWindow from './components/ChatWindow.vue'
import LoginView from './components/LoginView.vue'
import MemoryPanel from './components/MemoryPanel.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import { useAuth } from './composables/useAuth'
import { useChat } from './composables/useChat'
import { useConversations } from './composables/useConversations'
import { useMemories } from './composables/useMemories'

const { user, restoring, isLoggedIn, login, restore, logout, clear } = useAuth()
const conversations = useConversations()
const memories = useMemories()

const loginLoading = ref(false)
const loginError = ref('')
const loadingMessages = ref(false)
const memoryOpen = ref(false)
const chatInputRef = ref<InstanceType<typeof ChatInput>>()

/**
 * 记忆抽取是流结束后的**后台任务**，接口返回 done 时它多半还没写完。
 * 所以这里不立刻刷新，而是过几秒再拉一次，角标才会自然涨上去。
 * 用户点开面板时还会再拉一次，双保险。
 */
let memoryTimer: number | undefined

function scheduleMemoryRefresh(): void {
  window.clearTimeout(memoryTimer)
  memoryTimer = window.setTimeout(() => {
    void memories.refresh().catch(() => {
      // 后台刷新失败无所谓，下次打开面板会重试
    })
  }, 3000)
}

// 每轮对话结束后刷新侧边栏，让新建会话的自动标题和时间排序即时生效
const chat = useChat({
  onFinished: () => {
    void conversations.refresh()
    scheduleMemoryRefresh()
  },
})

/** 当前会话的标题（从侧边栏列表里取，保持与列表一致） */
function currentTitle(): string {
  const id = conversations.currentId.value
  return conversations.items.value.find((item) => item.id === id)?.title ?? ''
}

/** 拉取当前会话的历史消息 */
async function loadCurrent(): Promise<void> {
  const id = conversations.currentId.value
  if (!id) {
    chat.clear()
    return
  }
  loadingMessages.value = true
  try {
    await chat.load(id)
  } catch {
    // 会话不存在（别的标签页删了 / 超过 30 天被归档）时，退回空状态
    chat.clear()
  } finally {
    loadingMessages.value = false
  }
}

/** 登录成功后：拉列表 + 拉记忆 + 恢复上次打开的会话 */
async function bootstrap(): Promise<void> {
  await conversations.refresh()
  // 记忆拉取失败不该拦住登录流程，用 catch 兜住
  await memories.refresh().catch(() => {})
  // refresh 可能把失效的 currentId 换成第一个会话，也可能变成 null
  if (conversations.currentId.value) await loadCurrent()
  else chat.clear()
}

async function handleLogin(payload: { username: string; password: string }): Promise<void> {
  if (loginLoading.value) return
  loginLoading.value = true
  loginError.value = ''
  try {
    await login(payload.username, payload.password)
    await bootstrap()
  } catch (error) {
    loginError.value = error instanceof Error ? error.message : '登录失败，请重试'
  } finally {
    loginLoading.value = false
  }
}

function handleLogout(): void {
  chat.stop()
  chat.clear()
  conversations.reset()
  memories.reset()
  memoryOpen.value = false
  logout()
}

async function handleCreate(): Promise<void> {
  chat.stop()
  await conversations.create()
  chat.clear()
  chatInputRef.value?.focus()
}

async function handleSelect(id: string): Promise<void> {
  if (id === conversations.currentId.value) return
  // 切走时中断还在跑的流，避免回复写进另一个会话
  chat.stop()
  conversations.select(id)
  await loadCurrent()
}

async function handleRename(id: string, title: string): Promise<void> {
  await conversations.rename(id, title)
}

async function handleRemove(id: string): Promise<void> {
  const target = conversations.items.value.find((item) => item.id === id)
  const confirmed = window.confirm(`删除会话「${target?.title ?? ''}」？\n删除后不会再出现在列表里。`)
  if (!confirmed) return

  const wasCurrent = conversations.currentId.value === id
  if (wasCurrent) chat.stop()

  await conversations.remove(id)

  if (wasCurrent) {
    if (conversations.currentId.value) await loadCurrent()
    else chat.clear()
  }
}

async function handleClear(): Promise<void> {
  const id = conversations.currentId.value
  if (!id) return
  if (!window.confirm('清空这段对话的所有消息？会话本身会保留。')) return

  chat.stop()
  await conversations.clearMessages(id)
  chat.clear()
}

async function handleSend(text: string): Promise<void> {
  // 还没选会话就自动建一个，省掉"先点新建再打字"这一步
  let id = conversations.currentId.value
  if (!id) {
    const created = await conversations.create()
    id = created.id
  }
  await chat.send(id, text)
}

/** 打开记忆面板时重新拉一次：用户点开就想看到最新的，不能是几秒前的缓存 */
async function handleOpenMemories(): Promise<void> {
  memoryOpen.value = true
  await memories.refresh().catch(() => {})
}

async function handleRemoveMemory(id: number): Promise<void> {
  await memories.remove(id)
}

async function handleClearMemories(): Promise<void> {
  if (!window.confirm('清空全部记忆？\n之后 AI 不会再记得你的身份、偏好和背景信息。')) return
  await memories.clearAll()
}

onMounted(async () => {
  // 任何接口返回 401 都统一退回登录页，并清掉本地残留状态
  onUnauthorized(() => {
    chat.stop()
    chat.clear()
    conversations.reset()
    memories.reset()
    memoryOpen.value = false
    clear()
  })

  await restore()
  if (isLoggedIn.value) await bootstrap()
})

onUnmounted(() => {
  window.clearTimeout(memoryTimer)
})
</script>

<template>
  <div v-if="restoring" class="boot">正在恢复登录状态…</div>

  <LoginView
    v-else-if="!isLoggedIn || !user"
    :loading="loginLoading"
    :error="loginError"
    @submit="handleLogin"
  />

  <div v-else class="app-shell">
    <SessionSidebar
      :conversations="conversations.items.value"
      :current-id="conversations.currentId.value"
      :user="user"
      :loading="conversations.loading.value"
      @create="handleCreate"
      @select="handleSelect"
      @rename="handleRename"
      @remove="handleRemove"
      @logout="handleLogout"
    />

    <main class="main">
      <ChatWindow
        :title="currentTitle()"
        :messages="chat.messages.value"
        :has-conversation="Boolean(conversations.currentId.value)"
        :loading="loadingMessages"
        :memory-count="memories.items.value.length"
        @clear="handleClear"
        @open-memories="handleOpenMemories"
      />
      <ChatInput
        ref="chatInputRef"
        :disabled="chat.sending.value"
        :placeholder="
          conversations.currentId.value
            ? '输入消息，Enter 发送，Shift+Enter 换行'
            : '输入消息，会自动新建一个会话'
        "
        @send="handleSend"
      />
    </main>

    <!-- 记忆面板自己 Teleport 到 body，放在这里只是为了就近管理状态 -->
    <MemoryPanel
      :open="memoryOpen"
      :memories="memories.items.value"
      :loading="memories.loading.value"
      @close="memoryOpen = false"
      @remove="handleRemoveMemory"
      @clear="handleClearMemories"
    />
  </div>
</template>

<style scoped>
.boot {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  font-size: 14px;
  color: #999;
}

.app-shell {
  display: flex;
  height: 100vh;
  background: #fff;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0; /* 防止长内容把 flex 容器撑破 */
  min-height: 0;
  background: #f7f8fa;
}
</style>
