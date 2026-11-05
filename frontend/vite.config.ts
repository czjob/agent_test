import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置文件：开发服务器（npm run dev）和构建（npm run build）都读它

/**
 * 后端地址。
 * 必须用 127.0.0.1 而不是 localhost：
 * Windows 上 localhost 会优先解析到 IPv6 (::1)，uvicorn 默认只监听 IPv4，
 * Node 又没有 Happy Eyeballs 回退，代理会报 ECONNREFUSED ::1:8000。
 */
const BACKEND = 'http://127.0.0.1:8000'

/**
 * 需要转发到后端的路由前缀。
 * 新增一个后端业务模块就往这里加一行——比复制三段几乎相同的 proxy 配置更不容易漏。
 * 漏配的症状很隐蔽：请求不会被转发，而是命中 Vite 的 SPA fallback 返回 index.html，
 * 前端拿到一坨 HTML 去 res.json()，报的是 JSON 解析错误，完全看不出是代理的问题。
 */
const API_PREFIXES = ['/auth', '/conversations', '/chat', '/memories', '/documents']

export default defineConfig({
  plugins: [vue()], // Vue 单文件组件（.vue）插件：让 Vite 能解析 <script setup> 等语法
  server: {
    port: 5173, // 开发服务器端口，浏览器访问 http://localhost:5173

    // 开发环境代理，避免跨域问题。
    // 原理：前端请求 /chat/stream（相对路径）→ Vite dev server 收到后
    // 转发到 target 指向的后端 → 后端响应再原样转回前端。
    // 浏览器眼里始终只跟 5173 通信，所以不存在跨域。
    proxy: Object.fromEntries(
      API_PREFIXES.map((prefix) => [
        prefix,
        {
          target: BACKEND,
          changeOrigin: true, // 转发时把 Host 头改成目标地址的（部分后端会校验）
        },
      ])
    ),
  },
})
