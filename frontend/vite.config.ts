import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置文件：开发服务器（npm run dev）和构建（npm run build）都读它
export default defineConfig({
  plugins: [vue()],  // Vue 单文件组件（.vue）插件：让 Vite 能解析 <script setup> 等语法
  server: {
    port: 5173,  // 开发服务器端口，浏览器访问 http://localhost:5173
    // 开发环境代理，避免跨域问题（也可以后端配 CORS）
    // 原理：前端请求 /chat/stream（相对路径）→ Vite dev server 收到后
    // 转发到 target 指向的后端 → 后端响应再原样转回前端。
    // 浏览器眼里始终只跟 5173 通信，所以不存在跨域。
    // 注意：target 必须用 127.0.0.1，不能写 localhost
    // Windows 上 localhost 会优先解析到 IPv6 (::1)，而 uvicorn 默认只监听 IPv4，
    // Node 18 又没有 Happy Eyeballs 回退，代理会 ECONNREFUSED ::1:8000 -> 500
    proxy: {
      '/chat': {  // 匹配所有以 /chat 开头的请求路径
        target: 'http://127.0.0.1:8000',  // 后端地址
        changeOrigin: true,                // 转发时把 Host 头改成目标地址的（部分后端会校验）
      },
    },
  },
})
