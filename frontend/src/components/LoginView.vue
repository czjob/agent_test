<script setup lang="ts">
/**
 * 登录页。
 *
 * 组件本身不碰网络：提交时只把账号密码 emit 出去，由 App 调接口并回传 loading / error。
 * 这样这个组件可以独立预览、独立测试。
 */
import { ref } from 'vue'

const props = defineProps<{
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'submit', payload: { username: string; password: string }): void
}>()

const username = ref('')
const password = ref('')

// 种子账号，点一下直接填入，免去演示时手动输入
const demoAccounts = [
  { username: 'alice', display: '爱丽丝' },
  { username: 'bob', display: '鲍勃' },
  { username: 'carol', display: '卡罗尔' },
]

function fill(username0: string): void {
  username.value = username0
  password.value = 'demo1234'
}

function submit(): void {
  if (props.loading) return
  const name = username.value.trim()
  if (!name || !password.value) return
  emit('submit', { username: name, password: password.value })
}
</script>

<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <h1>LangChain Agent</h1>
      <p class="subtitle">登录后开始对话，会话与记忆会保存在数据库</p>

      <label>
        <span>用户名</span>
        <input v-model="username" type="text" autocomplete="username" placeholder="alice" />
      </label>

      <label>
        <span>密码</span>
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          placeholder="demo1234"
        />
      </label>

      <p v-if="error" class="error">{{ error }}</p>

      <button type="submit" :disabled="loading || !username.trim() || !password">
        {{ loading ? '登录中…' : '登录' }}
      </button>

      <div class="demo">
        <p>演示账号（密码均为 demo1234）</p>
        <div class="chips">
          <button
            v-for="account in demoAccounts"
            :key="account.username"
            type="button"
            class="chip"
            @click="fill(account.username)"
          >
            {{ account.display }}
          </button>
        </div>
      </div>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 360px;
  background: #fff;
  border-radius: 16px;
  padding: 32px 28px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

h1 {
  font-size: 20px;
  font-weight: 600;
  text-align: center;
}

.subtitle {
  font-size: 13px;
  color: #888;
  text-align: center;
  margin-bottom: 6px;
  line-height: 1.5;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #555;
}

input {
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s;
}

input:focus {
  border-color: #007aff;
}

.error {
  font-size: 13px;
  color: #e5484d;
  background: #fdecec;
  padding: 8px 10px;
  border-radius: 8px;
}

button[type='submit'] {
  margin-top: 4px;
  padding: 11px;
  border: none;
  border-radius: 8px;
  background: #007aff;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  transition: opacity 0.15s;
}

button[type='submit']:disabled {
  background: #b9d7f7;
  cursor: not-allowed;
}

.demo {
  margin-top: 8px;
  border-top: 1px solid #f0f0f0;
  padding-top: 14px;
}

.demo p {
  font-size: 12px;
  color: #aaa;
  text-align: center;
  margin-bottom: 8px;
}

.chips {
  display: flex;
  gap: 8px;
  justify-content: center;
}

.chip {
  padding: 5px 12px;
  font-size: 12px;
  border: 1px solid #dcdcdc;
  border-radius: 999px;
  background: #fff;
  color: #555;
  cursor: pointer;
}

.chip:hover {
  border-color: #007aff;
  color: #007aff;
}
</style>
