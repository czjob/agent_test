/**
 * 登录态管理。
 *
 * 为什么状态写在模块作用域（函数外面）而不是函数里面：
 *   composable 里如果每次调用都 ref()，每个组件都会拿到独立的一份状态。
 *   登录态是全局唯一的，放在模块作用域就是单例——任何组件调用 useAuth()
 *   拿到的都是同一份。这是 Vue 里最轻量的"全局状态"，不用引 Pinia。
 */

import { computed, ref } from 'vue'

import { fetchMe, login as loginApi } from '../api/auth'
import { getToken } from '../api/client'
import type { User } from '../types'

const user = ref<User | null>(null)

/** 是否还在用本地 token 校验登录态。true 时界面显示加载中，避免闪一下登录页 */
const restoring = ref(true)

export function useAuth() {
  const isLoggedIn = computed(() => user.value !== null)

  async function login(username: string, password: string): Promise<void> {
    user.value = await loginApi(username, password)
  }

  /**
   * 页面刷新后恢复登录态。
   *
   * 不做"本地有 token 就直接当登录成功"——token 可能早已过期。
   * 调一次 /auth/me 验证，失败就静默清空，让用户重新登录。
   */
  async function restore(): Promise<void> {
    if (!getToken()) {
      restoring.value = false
      return
    }
    try {
      user.value = await fetchMe()
    } catch {
      user.value = null
    } finally {
      restoring.value = false
    }
  }

  function logout(): void {
    user.value = null
  }

  /** 供 401 拦截器调用：token 失效时清空用户态 */
  function clear(): void {
    user.value = null
  }

  return { user, restoring, isLoggedIn, login, restore, logout, clear }
}
