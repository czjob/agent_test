/**
 * 网络层基础封装。
 *
 * 收拢三件所有请求都要做的事：
 *   1. 自动带上 Authorization 头（token 从 localStorage 取）
 *   2. 把非 2xx 响应统一转成 ApiError，并且把后端的 detail 文案带出来
 *   3. 遇到 401 统一清理登录态并通知上层跳登录页
 *
 * 不做的事：不在这里拼业务路径、不做重试、不做 loading 状态。
 */

const TOKEN_KEY = 'agent_demo_token'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

/** 401 回调。App 启动时注册成"清空用户态"，避免网络层依赖 UI 层 */
let unauthorizedHandler: (() => void) | null = null

export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler
}

function buildHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra)
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  return headers
}

/** 从错误响应体里抠出可读文案，抠不出来就用状态码兜底 */
async function extractErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
    // FastAPI 的 422 校验错误是数组结构，取第一条的 msg
    if (Array.isArray(body?.detail) && body.detail[0]?.msg) return body.detail[0].msg
  } catch {
    // 响应不是 JSON（比如网关返回的 HTML），走下面的兜底
  }
  return `请求失败（HTTP ${res.status}）`
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: buildHeaders(init.headers),
  })
  return handleResponse<T>(res)
}

/**
 * 上传文件（multipart/form-data）。
 *
 * 为什么不复用 api.post：
 *   那个函数会强制设 Content-Type: application/json。
 *   上传必须让浏览器自己生成 `multipart/form-data; boundary=...`，
 *   手工设置 Content-Type 会丢掉 boundary，后端解析出来是空的。
 *
 * 鉴权和错误处理仍然复用同一套，否则上传接口就成了 401 处理的漏洞。
 */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(path, { method: 'POST', body: form, headers })
  return handleResponse<T>(res)
}

/** 统一的响应处理：401 清理登录态、非 2xx 抛 ApiError、204 返回 undefined */
async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    setToken(null)
    unauthorizedHandler?.()
    throw new ApiError(401, '登录已过期，请重新登录')
  }

  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorMessage(res))
  }

  // 204 No Content（删除类接口）没有响应体，直接返回 undefined
  if (res.status === 204) return undefined as unknown as T

  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
