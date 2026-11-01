<script setup lang="ts">
import { ref, nextTick } from 'vue'

// ====================== 类型定义 ======================
interface Message {
  role: 'user' | 'assistant'
  content: string
  toolInfo?: string[]  // 工具调用信息
}

// ====================== 状态 ======================
// ref() 把普通数据变成"响应式"数据：值一变，用到它的界面自动更新
const messages = ref<Message[]>([])       // 消息列表，页面上的每条聊天气泡都来自它
const inputText = ref('')                 // 输入框里的文字（v-model 双向绑定）
const loading = ref(false)                // 是否正在等待回复（控制"回复中..."和光标动画）
// 会话 ID：每次刷新页面生成一个新的；后端靠它区分不同会话、存取多轮记忆
const sessionId = ref(`user_${Date.now()}`)
const chatContainer = ref<HTMLElement>()  // 消息列表 DOM 引用，用于自动滚动到底部

// ====================== 发送消息 + 流式接收 ======================
async function sendMessage() {
  // trim() 去掉首尾空格；空消息或正在等待回复时直接忽略，防止重复发送
  const text = inputText.value.trim()
  if (!text || loading.value) return

  // 1. 把用户消息 push 进列表（界面立刻出现蓝色气泡）
  //    注意：模板里用 v-for 遍历 messages，数组一变，Vue 的响应式系统自动重渲染
  messages.value.push({ role: 'user', content: text })
  inputText.value = ''  // 清空输入框

  // 2. 先放一条"空的助手消息"占位，后面收到 token 再逐段填充它的 content
  //    （先 push 空消息，是为了让前端立刻显示"正在回复"的状态和光标）
  messages.value.push({ role: 'assistant', content: '', toolInfo: [] })
  // 【Vue 3 响应式关键点】必须从响应式数组里把这条消息"读出来"再操作：
  //   messages 是 ref（深度响应式），直接 push 进去的原始对象并不受响应式管辖，
  //   后面改它的 content 不会触发界面重渲染（要等 loading 变化才顺带刷出来，
  //   表现就是"整段回答一下子出现"）。
  //   从数组里取出来的是响应式代理，改它才会每来一个 token 就重渲染一次。
  const assistantMsg: Message = messages.value[messages.value.length - 1]
  loading.value = true

  try {
    // 3. 调用后端流式接口
    //    这里用相对路径 '/chat/stream'，浏览器会带上当前域名 localhost:5173，
    //    由 vite.config.ts 里的 proxy 配置转发到后端 localhost:8000，
    //    这样前端代码里不用写死后端地址，也没有跨域问题
    const res = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },  // 告诉后端请求体是 JSON
      body: JSON.stringify({
        session_id: sessionId.value,
        input: text,
      }),
    })

    // fetch 默认会把整个响应体收完才 resolve；对于流式响应，
    // res.body 是一个 ReadableStream，可以边到边读，不用等结束
    if (!res.body) throw new Error('响应体为空')

    // 4. 读取流：getReader() 拿到读取器，read() 每次返回一到手的一个数据块
    //    value 是 Uint8Array（二进制字节），用 TextDecoder 按 UTF-8 解码成字符串
    const reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''  // 缓冲区：网络分包不保证按消息边界切，半条消息要攒着

    while (true) {
      const { done, value } = await reader.read()  // 等待下一个数据块（Promise）
      if (done) break  // 流关闭，退出循环

      // stream: true 表示"可能还有后续字节"，避免多字节汉字被从中间截断
      buffer += decoder.decode(value, { stream: true })

      // 按 SSE 格式分割（以 \n\n 为分隔）
      // 一条 SSE 消息的完整格式是 "data: {...}\n\n"，所以遇到空行就是一条完整消息
      const lines = buffer.split('\n\n')
      // pop() 取出最后一段：它可能是不完整的半条消息，留到下次数据到了再拼
      // （已经完整的若干条留在 lines 里，进入下面逐个处理）
      buffer = lines.pop() || ''

      for (const line of lines) {
        // 只处理 data: 开头的行（SSE 还支持 event:/id:/retry: 等字段，这里用不到）
        if (!line.startsWith('data: ')) continue

        // 去掉 "data: " 前缀（6 个字符），剩下的按 JSON 解析
        const data = JSON.parse(line.slice(6))

        if (data.type === 'token') {
          // 文本内容：后端逐 token 推送，每来一片就追加到助手消息上（打字机效果）
          assistantMsg.content += data.content
          scrollToBottom()
        } else if (data.type === 'tool_start') {
          // 工具调用开始：在气泡里插一条工具状态行
          assistantMsg.toolInfo?.push(data.content)
        } else if (data.type === 'tool_end') {
          // 工具调用结束：把工具返回值也展示出来
          assistantMsg.toolInfo?.push(data.content)
        } else if (data.type === 'error') {
          // 后端推送的错误事件：把错误信息拼进消息里展示
          assistantMsg.content += `\n❌ 错误：${data.content}`
        } else if (data.type === 'done') {
          // 流结束：什么都不用做，循环会自然结束
        }
      }
    }
  } catch (err: any) {
    // fetch 本身失败（网络错误、代理不通、后端 500 等）会进这里，
    // 而不是 SSE 的 error 事件（那是 HTTP 200 之后业务层的错误）
    assistantMsg.content += `\n❌ 请求失败：${err.message}`
  } finally {
    // 无论成功失败都要解除 loading（关闭"回复中..."和光标动画）
    loading.value = false
    scrollToBottom()
  }
}

// ====================== 自动滚动到底部 ======================
function scrollToBottom() {
  // nextTick：等 Vue 把 DOM 更新完（消息刚 push 进数组时 DOM 还没渲染），
  // 再把滚动条拉到最底，保证用户看到最新内容
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

// ====================== 回车发送 ======================
function handleKeydown(e: KeyboardEvent) {
  // Enter 直接发送；Shift+Enter 是换行（e.shiftKey 判断Shift是否按下）
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()  // 阻止 textarea 默认的换行行为
    sendMessage()
  }
}
</script>

<template>
  <!-- 整体布局：flex 纵向排列——头部 / 消息列表（占满剩余高度）/ 输入区 -->
  <div class="chat-app">
    <!-- 顶部标题栏 -->
    <header class="header">
      <h1>LangChain Agent 流式对话</h1>
      <!-- slice(-8) 只显示 session ID 后 8 位，避免太长 -->
      <span class="session-id">Session: {{ sessionId.slice(-8) }}</span>
    </header>

    <!-- 消息列表：v-for 按索引遍历，:key="idx" 帮 Vue 高效 diff -->
    <div ref="chatContainer" class="chat-container">
      <!-- 空状态提示：一条消息都没有时显示 -->
      <div v-if="messages.length === 0" class="empty-hint">
        <p>👋 你好！我是你的 AI 助手</p>
        <p class="tip">试试问我：现在几点？或者帮我算一下 1024 * 8</p>
      </div>

      <!-- v-for 按索引遍历消息数组，:key 帮 Vue 高效 diff；
           :class="msg.role" 让用户/助手用两种气泡样式 -->
      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        class="message"
        :class="msg.role"
      >
        <div class="avatar">{{ msg.role === 'user' ? '🧑' : '🤖' }}</div>
        <div class="bubble">
          <!-- 工具调用信息：v-if 控制有无；有调用时才显示橙色状态区 -->
          <div v-if="msg.toolInfo && msg.toolInfo.length > 0" class="tool-info">
            <div v-for="(tool, i) in msg.toolInfo" :key="i" class="tool-line">
              {{ tool }}
            </div>
          </div>
          <!-- 文本内容：white-space: pre-wrap 保留换行 -->
          <div class="content">{{ msg.content }}</div>
          <!-- 流式输出中的光标：只有最后一条助手消息 + loading 时才闪烁 -->
          <span v-if="loading && idx === messages.length - 1 && msg.role === 'assistant'" class="cursor">▋</span>
        </div>
      </div>
    </div>

    <!-- 输入框 -->
    <div class="input-area">
    <!-- 输入框：v-model 双向绑定（输入自动同步到 inputText）；
         @keydown 处理回车发送；等待回复时 :disabled 禁止输入 -->
    <textarea
      v-model="inputText"
      @keydown="handleKeydown"
      placeholder="输入消息，Enter 发送，Shift+Enter 换行"
      :disabled="loading"
      rows="2"
    />
      <button @click="sendMessage" :disabled="loading || !inputText.trim()">
        {{ loading ? '回复中...' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
/* scoped 表示这些样式只作用于本组件，不会污染全局 */

.chat-app {
  display: flex;
  flex-direction: column;   /* 纵向排列：header / container / input-area */
  height: 100vh;            /* 占满整个视口高度 */
  max-width: 800px;         /* 内容区最大宽度，居中更像聊天软件 */
  margin: 0 auto;
  background: #f5f5f5;
}

.header {
  display: flex;
  justify-content: space-between;  /* 标题左、Session 右 */
  align-items: center;
  padding: 16px 20px;
  background: #fff;
  border-bottom: 1px solid #e0e0e0;
}
.header h1 {
  font-size: 18px;
  margin: 0;
}
.session-id {
  font-size: 12px;
  color: #999;
}

.chat-container {
  flex: 1;               /* 占满 header 和 input-area 之外的剩余高度 */
  overflow-y: auto;      /* 消息多了出现滚动条（配合 scrollToBottom 自动滚底） */
  padding: 20px;
}

.empty-hint {
  text-align: center;
  margin-top: 80px;
  color: #666;
}
.empty-hint .tip {
  font-size: 14px;
  color: #999;
  margin-top: 8px;
}

.message {
  display: flex;
  margin-bottom: 16px;
  gap: 10px;  /* 头像和气泡之间的间距 */
}
.message.user {
  flex-direction: row-reverse;  /* 用户消息：头像移到右边 */
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;      /* 正圆头像 */
  background: #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: center; /* emoji 居中 */
  font-size: 18px;
  flex-shrink: 0;          /* 窄屏时不被压缩 */
}

.bubble {
  max-width: 70%;          /* 气泡最宽占行宽 70%，长文本自动换行 */
  padding: 10px 14px;
  border-radius: 12px;
  background: #fff;
  line-height: 1.6;
  white-space: pre-wrap;   /* 保留文本里的换行和空格 */
  word-break: break-word;  /* 长单词/URL 强制断行，防止溢出 */
}
.message.user .bubble {
  background: #007aff;     /* 用户气泡蓝色 */
  color: #fff;
}

/* 工具调用状态区：橙黄色底，和正文区分 */
.tool-info {
  font-size: 12px;
  color: #ff9500;
  background: #fff8e6;
  padding: 6px 8px;
  border-radius: 6px;
  margin-bottom: 8px;
}
.tool-line {
  margin: 2px 0;
}

/* 流式输出光标：闪烁动画，模拟"正在打字" */
.cursor {
  animation: blink 1s infinite;
  color: #007aff;
}
@keyframes blink {
  0%, 50% { opacity: 1; }    /* 0~50% 可见 */
  51%, 100% { opacity: 0; }  /* 51%~100% 隐藏，形成闪烁 */
}

.input-area {
  display: flex;
  gap: 10px;
  padding: 16px 20px;
  background: #fff;
  border-top: 1px solid #e0e0e0;
}
.input-area textarea {
  flex: 1;                  /* 输入框占满按钮之外的宽度 */
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  resize: none;             /* 禁止手动拖拽改变大小（用 rows 固定 2 行） */
  font-size: 14px;
  outline: none;            /* 去掉聚焦时的默认蓝框 */
}
.input-area textarea:focus {
  border-color: #007aff;    /* 聚焦时边框变蓝 */
}
.input-area button {
  padding: 0 24px;
  border: none;
  border-radius: 8px;
  background: #007aff;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}
.input-area button:disabled {
  background: #ccc;         /* 禁用态灰色 */
  cursor: not-allowed;
}
</style>
