# LangChain Agent 多用户对话 Demo

**Vue3 + FastAPI + LangChain Agent + PostgreSQL** 的完整可运行项目。
支持流式输出、工具调用、多用户隔离、会话列表、以及真正落库的对话记忆。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite |
| 后端 | FastAPI + Uvicorn |
| Agent | 手写 Agent 循环（bind_tools + astream 逐 token 流式） |
| 流式协议 | SSE (Server-Sent Events) |
| 数据库 | PostgreSQL 16 + SQLAlchemy 2.0 async + asyncpg |
| 认证 | JWT (PyJWT) + argon2 密码哈希 (pwdlib) |
| 模型 | DeepSeek（OpenAI 兼容协议，可换） |

## 功能特性

- ✅ 流式输出（SSE 逐 token 推送，打字机效果）
- ✅ 工具调用（数学计算器、获取当前时间），调用过程实时展示
- ✅ **多用户**：登录隔离，每个用户只能看到自己的会话与消息
- ✅ **会话管理**：新建、切换、重命名、删除、清空，左侧历史列表按时间分组
- ✅ **持久化记忆**：刷新页面 / 重启服务都不丢，历史从数据库读取
- ✅ **L2 上下文压缩**：消息超过 24 条时，把早期对话压成摘要替代原文，长对话不再丢前文
- ✅ **L3 长期记忆**：每轮结束后自动抽取用户身份 / 偏好 / 背景，跨会话生效
- ✅ **记忆面板**：看得见 AI 记住了什么，可单条删除或全部清空，删掉的不会被重新抽回来
- ✅ **会话生命周期**：列表只展示最近 30 天，过期自动归档，再过期物理删除
- ✅ 上下文窗口控制：最多带最近 40 条消息给模型，避免超出模型上下文

## 项目结构

```
agent-demo/
├── backend/
│   ├── main.py                 应用装配：中间件 / 路由 / 生命周期 / 清理任务
│   ├── config.py               所有环境变量集中读取
│   ├── db.py                   引擎、会话工厂、建表 + 轻量迁移
│   ├── models.py               ORM：User / Conversation / Message / Memory
│   ├── schemas.py              接口的请求响应模型
│   ├── security.py             密码哈希 + JWT + 当前用户依赖
│   ├── seed.py                 种子用户
│   ├── llm.py                  模型客户端单例（对话 / 摘要 / 抽取共用）
│   ├── background.py           后台任务托管（持引用防 GC + 退出时 drain）
│   ├── agent.py                Agent 循环 + 系统提示拼装（只管对话，不碰数据库）
│   ├── tools.py                工具定义
│   ├── routers/
│   │   ├── auth.py             POST /auth/login、GET /auth/me
│   │   ├── conversations.py    会话 CRUD
│   │   ├── chat.py             POST /chat/stream（SSE）
│   │   └── memories.py         长期记忆的查看与删除
│   └── services/
│       ├── conversation.py     会话与消息的数据访问 + 清理任务
│       ├── summary.py          L2 上下文压缩
│       └── memory.py           L3 记忆抽取、注入、面板查询
└── frontend/
    └── src/
        ├── App.vue             布局装配 + 事件编排
        ├── api/                client（token/401）/ auth / conversations / chat(SSE) / memories
        ├── composables/        useAuth / useConversations / useChat / useMemories
        ├── components/         LoginView / SessionSidebar / SessionItem / UserMenu /
        │                       ChatWindow / MessageBubble / ChatInput / MemoryPanel
        ├── types/              共享类型
        └── utils/date.ts       时间分组与格式化
```

## 快速启动

### 第一步：启动 PostgreSQL

项目用带 pgvector 扩展的 PG 镜像（P2 做 RAG 时不需要换库）：

```bash
# 国内直连 Docker Hub 通常超时，先走镜像源再打 tag
docker pull docker.m.daocloud.io/pgvector/pgvector:pg16
docker tag docker.m.daocloud.io/pgvector/pgvector:pg16 pgvector/pgvector:pg16

docker run -d --name agent-pg -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=agent_demo -e TZ=Asia/Shanghai -v agent_pgdata:/var/lib/postgresql/data --restart unless-stopped pgvector/pgvector:pg16
```

建表、建扩展、写种子用户都由后端启动时自动完成，不需要手工执行 SQL。

### 第二步：启动后端

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

启动日志里出现 `数据库就绪` 和 `已创建种子用户` 即为正常。
访问 http://localhost:8000/docs 可以看到全部接口文档。

数据库地址、密钥等配置都在 `backend/.env`，首次运行请按需修改 `JWT_SECRET`。

### 第三步：启动前端

```bash
cd frontend
npm install
npm run dev
```

访问终端提示的地址（默认 http://localhost:5173）。

### 演示账号

| 用户名 | 密码 | 显示名 |
|---|---|---|
| alice | demo1234 | 爱丽丝 |
| bob | demo1234 | 鲍勃 |
| carol | demo1234 | 卡罗尔 |

想增删账号就改 `backend/.env` 里的 `SEED_USERS`（格式 `用户名:密码:显示名`，逗号分隔），重启后端生效。

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录换 token |
| GET | `/auth/me` | 用 token 换当前用户 |
| GET | `/conversations` | 会话列表（最近 30 天） |
| POST | `/conversations` | 新建会话 |
| GET | `/conversations/{id}` | 会话详情 + 全部消息 |
| PATCH | `/conversations/{id}` | 重命名 |
| DELETE | `/conversations/{id}` | 删除（软删） |
| DELETE | `/conversations/{id}/messages` | 清空消息（同时清掉摘要） |
| POST | `/chat/stream` | 流式对话（SSE） |
| GET | `/memories` | 当前用户的长期记忆 |
| DELETE | `/memories/{id}` | 忘记某一条 |
| DELETE | `/memories` | 清空全部记忆 |

除 `/auth/login` 外全部需要 `Authorization: Bearer <token>`。

## 核心设计说明

### 1. 流式输出原理

不用 langchain 1.x 的 `create_agent`——它内部用 `ainvoke` 调模型，拿不到 token 级分片。

```
while True:
    1. model.astream(messages)      # bind_tools 后的模型，逐 token 返回 AIMessageChunk
    2. 每个分片立刻 yield 成 SSE token 事件推给前端   -> 打字机效果
    3. 分片用 + 累加成完整 AIMessage，得到完整 tool_calls
    4. 有 tool_calls -> 逐个执行工具，ToolMessage 追加进 messages，继续循环
    5. 没有 tool_calls -> 就是最终回答，结束
```

### 2. 为什么流式接口不持有数据库连接

FastAPI 的 `Depends(get_db)` 要等响应彻底结束才释放 session。对 SSE 这种几十秒的长连接来说，
等于一条流占着一个连接。所以 `routers/chat.py` 分成两段：

```
准备阶段：开一个短 session，校验归属 -> 存用户消息 -> 读历史 -> 关闭
流式阶段：完全不碰数据库
落库阶段：开一个新 session，把本轮产生的 AI/工具消息写进去
```

同理 `get_current_user` 也是自己开短 session，不依赖请求级 session。

### 3. L2 上下文压缩：摘要替代原文

长对话的直接问题：把 40 条历史原文塞进 prompt 要几千 token，而一旦超出窗口被截断，早期信息就永久丢失——用户问"我最开始说的那个需求"，模型完全不知道指什么。

做法是"摘要 + 未压缩区"：

```
喂给模型的上下文 = 系统提示(摘要 + 记忆) + 摘要线之后的原文（最近 10 条左右）
```

- `conversations.summary` 存摘要，`summary_until_seq` 记录**摘要覆盖到哪条消息**
- 没有 `summary_until_seq` 就不知道哪些原文已经被吸收，每次都得重压整段历史
- **原文一条都不删**，摘要只影响喂给模型的那份上下文，前端照样显示完整历史
- 增量压缩：新摘要 = 旧摘要 + 新增的早期消息，不是每轮从头压

关键一点：**压缩跑在后台**。它要调一次模型（1~3 秒），放在流式响应之前会让用户多等这么久才看到第一个字。放在流结束之后跑，这一轮用旧摘要、下一轮用新摘要，用户感知不到延迟。

清空消息时会同时清掉摘要——摘要描述的是已被删掉的内容，留着它模型就会拿一段不存在的历史回答问题。

### 4. L3 长期记忆：跨会话的用户画像

和 L2 的分工很明确：

| | 作用域 | 回答的问题 |
|---|---|---|
| L2 summary | 单个会话 | "我们刚才聊到哪了" |
| L3 memory | 跨会话 | "这个人是谁、他喜欢什么" |

每轮流式结束后异步抽一次，模型返回 JSON：

```json
{"memories": [{"kind": "preference", "content": "希望回答简洁", "importance": 0.8}]}
```

三个工程细节：

1. **去重靠数据库唯一约束** `(user_id, content)` + `ON CONFLICT DO UPDATE`。抽取是后台任务，同一个用户可能两轮对话同时在抽，先查再插会撞车（查的时候都没有，插的时候冲突）。冲突时取 `GREATEST(旧, 新)` 的重要度——被反复提及说明更重要。
2. **偏好和事实在 prompt 里分开写**。这两类记忆效力不同：`"喜欢简洁的回答"` 是对输出形式的硬要求，必须压过模型默认风格；`"是前端工程师"` 只是背景。混在一个列表里写，模型会一视同仁当"参考信息"，偏好就失灵了——实测混着写时问"什么是虚拟 DOM"照样输出六百字带小标题的长文，分开写并标注"必须遵守"后缩短到两句。
3. **删除是软删除**。抽取每轮都在跑，物理删掉之后下一轮只要又提到同样内容，模型就当"新信息"写回来，用户会觉得"删了怎么又出现了"。所以打 `dismissed_at` 标记，抽取时把已删内容放进"不要重复提取"名单，再做一次确定性过滤兜底。这类行没有保留价值，清理任务会定期物理删除 30 天前的记录。

### 5. 后台任务为什么不能直接 `create_task`

`asyncio.create_task(coro)` 返回的 Task 如果没有任何强引用，事件循环只持**弱引用**，垃圾回收随时可能把它回收掉，任务就静默消失了（Python 官方文档专门警告过这一点）。

所以 `background.py` 在模块级用 `set` 持住所有在跑的任务，完成时自动移除。另外提供 `drain()`：应用关闭时带超时地等一会儿，避免把正在写库的摘要/抽取任务硬中断。

### 6. 会话生命周期是两阶段

| 阶段 | 条件 | 动作 |
|---|---|---|
| 归档 | 超过 `ARCHIVE_AFTER_DAYS`(30) 天没更新 | 打 `archived_at`，列表不再返回，数据保留 |
| 删除 | 归档又超过 `PURGE_AFTER_DAYS`(30) 天 | 物理删除，消息随外键级联删除 |
| 软删 | 用户主动删除 | 打 `deleted_at`，数据保留可恢复 |

清理任务在 `main.py` 的 lifespan 里用 `asyncio` 循环执行，每 6 小时一次，没有引入额外依赖。
同一个循环顺带物理清除 30 天前"已被用户删除"的记忆行。

### 7. 越权防护

所有"按 id 查"的 SQL 都带 `user_id` 条件，而不是先查出来再在代码里判断。
不属于当前用户的会话统一返回 **404**（不是 403）——403 等于告诉对方"这个 id 存在，只是你没权限"。

### 8. 前端状态分层

| 状态 | 归属 | 说明 |
|---|---|---|
| 登录态 | `useAuth`（模块级单例） | 任何组件拿到的都是同一份 |
| 会话列表 / currentId | `useConversations`（单例） | 侧边栏和聊天窗口共享 |
| 长期记忆 | `useMemories`（单例） | 头部角标和记忆面板共享 |
| 消息 / 发送状态 | `useChat`（App 持有） | 跟着当前会话切换 |

不引 Pinia 的原因：模块作用域 + ref 已经够用，少一层概念。

## 常见问题

**Q: 拉镜像报 `invalid reference format`？**
A: 在 cmd 里粘贴了 PowerShell 的反引号换行符。cmd 用 `^` 续行，最简单的办法是把 `docker run` 写成单行。

**Q: `pip install` 后仍报 `No module named 'asyncpg'`？**
A: 机器上可能有多个 Python。用 `python -c "import sys;print(sys.executable)"` 确认当前解释器，
再对它执行 `python -m pip install -r requirements.txt`。本项目请在同一个环境里装依赖和启动服务。

**Q: 前端发消息报 500 / 代理连不上？**
A: Windows + Node 18 的 IPv6 坑：vite 代理 target 必须写 `http://127.0.0.1:8000`（本项目已配置）。
另外注意 vite 可能因为 5173 被占用而换到 5174，请以终端提示的地址为准。

**Q: 登录后列表是空的？**
A: 正常。新用户没有任何会话，点「新建会话」或直接输入消息（会自动建会话）。

**Q: 为什么聊完天记忆面板没马上出现新条目？**
A: 记忆抽取是流结束后触发的**后台任务**，要调一次模型（1~3 秒）。页面会在几秒后自动刷新角标，
打开面板也会重新拉一次，稍等即可。

**Q: 记忆会消耗多少 token？**
A: 每轮多一次抽取调用（输入约 500~1500 token，输出很短）。会话超过 24 条后，每隔若干轮多一次摘要调用。
都可以在 `.env` 里调阈值，或设 `MEMORY_ENABLED=false` 关掉 L3。

**Q: 想让 AI 忘掉某件事？**
A: 打开记忆面板删掉那一条即可。删除是软删除——之后即使你再提到同样的内容，也不会被重新抽回来。

## 下一步

- [ ] 记忆 L4：`memories` 加 `vector(1024)` 列，按语义相似度 top-k 召回，不再全量注入
- [ ] RAG：文档上传 + 切分 + 向量检索，检索封装成 `search_knowledge_base` 工具（Agent 主循环不用改）
- [ ] 记忆提炼：把同类记忆合并去重（现在"在杭州"和"在杭州工作"会同时存在）
- [ ] 会话标题用模型生成（现在是首条消息截断）
- [ ] 部署：Docker Compose 打包前后端
