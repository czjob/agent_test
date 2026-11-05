/** 认证接口。 */

import type { LoginResponse, User } from '../types'
import { api, setToken } from './client'

export async function login(username: string, password: string): Promise<User> {
  const data = await api.post<LoginResponse>('/auth/login', { username, password })
  setToken(data.access_token)
  return data.user
}

/** 刷新页面时用本地 token 换取当前用户，验证 token 是否还有效 */
export async function fetchMe(): Promise<User> {
  return api.get<User>('/auth/me')
}
