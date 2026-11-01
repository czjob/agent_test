# LangChain Agent 流式对话 Demo

一个完整可运行的 **Vue3 + FastAPI + LangChain Agent** 小项目，支持流式输出、工具调用、多轮对话记忆。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite |
| 后端 | FastAPI + Uvicorn |
| Agent | 手写 Agent 循环（bind_tools + astream 逐 token 流式） |
| 流式协议 | SSE (Server-Sent Events) |
| 模型 | DeepSeek（OpenAI 兼容协议，可换） |

## 功能特性

- ✅ 流式输出（SSE 长连接 + 逐 token 推送，打字机效果）
- ✅ 工具调用（数学计算器、获取当前时间）
- ✅ 多轮对话记忆（按 session_id 隔离）
- ✅ 工具调用状态实时展示
- ✅ 前端开发代理，无跨域问题

## 项目结构

```
agent-demo/
├── backend/                # 后端：FastAPI + LangChain
│   ├── main.py             # 主程序（Agent + SSE 流式接口）
│   └── requirements.txt    # Python 依赖
├── frontend/               # 前端：Vue3 + TS
│   ├── src/
│   │   ├── App.vue         # 对话界面 + 流式渲染
│   │   ├── main.ts
│   │   └── style.css
│   ├── package.json
│   ├── vite.config.ts
│   └── index.html
└── README.md
```

## 快速启动

### 第一步：启动后端

```bash
cd backend

# 安装依赖
pip install -r requirements.txt

# 修改 main.py 里的 api_key（第 28 行附近）
# 如果用国内模型（如 DeepSeek），取消 base_url 注释并改成对应地址

# 启动服务
uvicorn main:app --reload --port 8000
```

后端启动成功后，访问 http://localhost:8000 应该看到 `{"status":"ok"}`。

### 第二步：启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务
npm run dev
```

前端启动后，访问终端里提示的地址（一般是 http://localhost:5173）。

## 测试对话

启动后可以试试这些问题：

1. **简单对话**：你好，你是谁？
2. **工具调用 - 时间**：现在几点了？
3. **工具调用 - 计算**：帮我算一下 256 * 32 等于多少？
4. **多轮记忆**：
   - 第一轮：我叫小王，是前端工程师
   - 第二轮：我是做什么的？
5. **组合任务**：现在几点？再过3小时是几点？再算一下 1024 * 8

## 核心知识点说明

### 后端流式输出原理

本项目**手写 Agent 循环**（不用 langchain 1.x 预置的 create_agent，因为它内部用 `ainvoke` 调模型，拿不到 token 级分片）：

```
while True:
    1. model.astream(messages)      # bind_tools 后的模型，逐 token 返回 AIMessageChunk
    2. 每个分片立刻 yield 成 SSE token 事件推给前端   -> 打字机效果
    3. 分片用 + 累加成完整 AIMessage，得到完整 tool_calls
    4. 有 tool_calls -> 逐个执行工具，ToolMessage 追加进 messages，继续循环
    5. 没有 tool_calls -> 就是最终回答，保存历史，结束
```

- 流式调用：`llm.bind_tools(tools)` 后 `model.astream(messages)`
- 多轮记忆：进程内 dict 按 `session_id` 存消息历史（重启即丢，生产换 Redis/DB）
- 打字机节奏：`_TOKEN_MIN_INTERVAL`（默认 30ms）给每个 token 的推送加最小间隔——模型生成快时几毫秒就吐一个 token，短回答会瞬间刷完看不出流式效果；设为 0 则完全跟随模型速度
- 通过 FastAPI 的 `StreamingResponse` + 异步生成器以 SSE 协议推送给前端

### 前端流式接收原理

使用原生 `fetch` + `ReadableStream` 读取响应流，按 SSE 格式（`data: {...}\n\n`）逐块解析，每收到一个 token 就追加到当前消息上，实现打字机效果。

## 常见问题

**Q: 报 CORS 跨域错误？**
A: 后端已经配了 CORS 中间件，前端 vite 也配了代理，正常不会有问题。如果还是报错，检查前后端端口是否对应。

**Q: 流式输出没效果，等很久才一次性出来？**
A: 本项目用手写 Agent 循环（`model.astream()`），本身就是逐 token 推送。如果变成整条出来，检查是否误改回了预置 agent（create_agent 内部是 ainvoke，非流式）。

**Q: 前端发消息报 500 Internal Server Error？**
A: 大概率是 Windows + Node 18 的 IPv6 坑：vite 代理 target 写 `localhost` 会优先解析到 `::1`，而 uvicorn 只监听 IPv4 `127.0.0.1`，Node 18 无 Happy Eyeballs 回退导致代理连接被拒。解决：代理 target 改成 `http://127.0.0.1:8000`（本项目已修复），或后端启动加 `--host ::`。

**Q: 工具不调用？**
A: 检查工具的函数名和 docstring 写得是否清晰，大模型靠这些信息判断是否调用工具。

## 下一步可以扩展

- [ ] 加 RAG 知识库（Chroma 向量数据库）
- [ ] 加文件上传功能
- [ ] 加会话历史侧边栏
- [ ] 部署上线（Docker 打包 + 静态文件托管）
