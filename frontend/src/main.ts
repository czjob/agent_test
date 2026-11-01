// 前端入口文件：Vue 应用的启动点
// index.html 里 <script src="/src/main.ts"> 引用的就是它
import { createApp } from 'vue'   // Vue 3 的应用工厂函数
import App from './App.vue'       // 根组件（整个聊天界面）
import './style.css'              // 全局样式（重置默认边距等）

// createApp(App)：用根组件创建一个应用实例
// .mount('#app')：把应用挂载到 index.html 里 id="app" 的 DOM 节点上
createApp(App).mount('#app')
