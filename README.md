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
| 数据库 | PostgreSQL 16 + pgvector + SQLAlchemy 2.0 async + asyncpg |
| 检索 | 向量检索（pgvector HNSW）+ bge-m3 embedding |
| 认证 | JWT (PyJWT) + argon2 密码哈希 (pwdlib) |
| 模型 | DeepSeek（对话）+ 硅基流动 bge-m3（向量），均为 OpenAI 兼容协议 |

## 功能特性

- ✅ 流式输出（SSE 逐 token 推送，打字机效果）
- ✅ 工具调用（数学计算器、获取当前时间），调用过程实时展示
- ✅ **多用户**：登录隔离，每个用户只能看到自己的会话与消息
- ✅ **会话管理**：新建、切换、重命名、删除、清空，左侧历史列表按时间分组
- ✅ **持久化记忆**：刷新页面 / 重启服务都不丢，历史从数据库读取
- ✅ **L2 上下文压缩**：消息超过 24 条时，把早期对话压成摘要替代原文，长对话不再丢前文
- ✅ **L3 长期记忆**：每轮结束后自动抽取用户身份 / 偏好 / 背景，跨会话生效
- ✅ **记忆面板**：看得见 AI 记住了什么，可单条删除或全部清空，删掉的不会被重新抽回来
- ✅ **RAG 知识库**：上传 txt / md / pdf，自动切分向量化，提问时 Agent 主动检索原文
- ✅ **引用可追溯**：检索结果带文件名和段号，模型回答时能指明出处
- ✅ **会话生命周期**：列表只展示最近 30 天，过期自动归档，再过期物理删除
- ✅ 上下文窗口控制：最多带最近 40 条消息给模型，避免超出模型上下文

## 项目结构

```
agent-demo/
├── backend/
│   ├── main.py                 应用装配：中间件 / 路由 / 生命周期 / 清理任务
│   ├── config.py               所有环境变量集中读取
│   ├── db.py                   引擎、会话工厂、建表 + 轻量迁移
│   ├── models.py               ORM：User / Conversation / Message / Memory / Document / Chunk
│   ├── schemas.py              接口的请求响应模型
│   ├── security.py             密码哈希 + JWT + 当前用户依赖
│   ├── seed.py                 种子用户
│   ├── llm.py                  对话模型客户端单例
│   ├── embeddings.py           向量化客户端（批量 / 重试 / 维度校验 / 降级）
│   ├── background.py           后台任务托管（持引用防 GC + 退出时 drain）
│   ├── agent.py                Agent 循环 + 系统提示拼装（只管对话，不碰数据库）
│   ├── tools.py                工具定义（含 RAG 检索工具 + 用户上下文注入）
│   ├── routers/
│   │   ├── auth.py             POST /auth/login、GET /auth/me
│   │   ├── conversations.py    会话 CRUD
│   │   ├── chat.py             POST /chat/stream（SSE）
│   │   ├── memories.py         长期记忆的查看与删除
│   │   └── documents.py        知识库上传 / 列表 / 删除 / 重建索引
│   └── services/
│       ├── conversation.py     会话与消息的数据访问 + 清理任务
│       ├── summary.py          L2 上下文压缩
│       ├── memory.py           L3 记忆抽取、注入、面板查询、向量召回
│       └── knowledge.py        RAG：文本解析 → 切分 → 向量化 → 检索
└── frontend/
    └── src/
        ├── App.vue             布局装配 + 事件编排
        ├── api/                client（token/401/上传）/ auth / conversations / chat / memories / documents
        ├── composables/        useAuth / useConversations / useChat / useMemories / useDocuments
        ├── components/         LoginView / SessionSidebar / SessionItem / UserMenu /
        │                       ChatWindow / MessageBubble / ChatInput / MemoryPanel / KnowledgePanel
        ├── types/              共享类型
        └── utils/date.ts       时间分组与格式化
```

## 快速启动

### 第一步：启动 PostgreSQL

项目用带 pgvector 扩展的 PG 镜像——RAG 的向量检索直接用数据库做，不需要额外部署向量库：

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

**P2 的 RAG 还需要一个向量模型的 Key**（不配也能正常聊天，只是知识库和记忆向量召回不可用）：

1. 去 https://cloud.siliconflow.cn/me/account/ak 注册并**完成实名认证**，创建 API Key
2. 填进 `backend/.env` 的 `EMBED_API_KEY=`，重启后端
3. 模型名写全 `BAAI/bge-m3`，注意列表里还有一个 `Pro/BAAI/bge-m3` 是**收费版**

> 换别的向量模型时，`EMBED_DIM` 必须同步改（bge-m3 是 1024，智谱 embedding-3 是 2048），
> 而且数据库里的向量列维度是建表时写死的，老库要 `ALTER COLUMN` 并重建索引。
> 启动日志会检查维度是否一致，不一致会直接打告警。

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
| GET | `/documents` | 知识库文档列表（含处理状态） |
| POST | `/documents` | 上传文档（multipart，返回 pending） |
| DELETE | `/documents/{id}` | 删除文档（切片级联删除） |
| POST | `/documents/{id}/reindex` | 重新切分并向量化 |

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

### 8. RAG：检索做成工具，而不是每轮注入

接入知识库有两条路，这个项目选了后者：

| | 做法 | 问题 |
|---|---|---|
| 注入式 | 每轮先把 top-k 文档塞进 system prompt | 实现简单，但每轮都带一堆无关内容，白烧 token，还会干扰模型判断 |
| 工具式 | 注册成 `search_knowledge_base` 工具，模型自己决定要不要查 | 需要模型判断准确，但只在真正需要时付检索成本 |

工具式的代价是模型可能偷懒不调，所以在工具的 docstring 里写清了"什么时候必须调用"——
这条 docstring 是写给模型看的，不是给人看的。对 Agent 主循环来说这是零改动：
`tools.py` 加一个函数，`agent.py` 一行没动。

**权限怎么保证**：工具需要知道"当前是哪个用户在查"，但 `user_id` 绝不能作为工具参数——
参数是模型生成的，模型可能被提示注入诱导，把 `user_id` 换成别人的。
所以用 `ContextVar` 在请求进入时由服务端注入，模型的输入里根本没有这个字段。
这和"越权防线写在 SQL 里"是同一条原则：能不让模型碰的东西就不让它碰。

**上传为什么是异步的**：一份 PDF 切几十段、每段调一次 embedding，全程十几秒。
同步等待会让请求长时间挂起，用户看到的是一个转不停的圈。所以上传接口只做
解析 + 落库 + 返回 `pending`，向量化丢给后台任务，前端轮询状态。

**切分不按固定长度硬切**：中文没有空格分词，按 500 字符硬切十有八九从句子中间断开，
检索出来给模型看的片段开头半句话，它就会当成完整信息用。
所以按空行分段、按句子边界聚合，相邻切片保留 80 字重叠——
答案刚好横跨两个切片时，让"答案的尾巴"和"答案的开头"都能被某个切片完整覆盖。

**Markdown 标题是硬边界**。这一条是实测踩出来的：一份 491 字、有四个小节的笔记，
因为没超过切片长度被聚成了一整段，问"红烧肉怎么做"召回的是包含微前端和连接池的整篇。
现在标题处无条件断开，每个切片自带小标题，向量里就带上了"这段讲什么"。
同时"只有一行标题、没有正文"的块不单独成片（文档大标题会白白占掉一个 top-k 名额），
而是作为上下文并进下一节。

**HNSW 而不是 IVFFlat**：IVFFlat 需要先用已有数据"训练"聚类中心，表空的时候建索引没有意义，
新写入的数据还可能落在没被覆盖的区域导致查不到。HNSW 是图结构，插入即生效，
适合"用户随时上传、随时想问"的场景。代价是建索引更慢、占内存更多，这个规模扛得住。

**HNSW 有个 2000 维的硬上限**，这是选模型时必须先确认的一件事：
`vector` 类型超过 2000 维建不了 HNSW 索引，而且 DDL 失败会把整个建表事务拖成 aborted。
bge-m3 是 1024 维（没事），智谱 `embedding-3` 默认 2048 维、OpenAI `3-large` 是 3072 维，
都超了。现在代码里做了检测：维度超限就跳过索引改走顺序扫描（结果正确，只是变慢），
并打印如何处理的提示；或者用 `EMBED_DIMENSIONS` 让接口少输出点维度。

**相似度阈值只能挡"离题"，挡不住"同文档的邻近章节"**。
实测一份 4 章节笔记、17 个查询：Top-1 命中 10/10，但正确命中的最低分是 0.49，
而邻近章节作噪声时能到 0.58 —— 两个区间重叠。
所以别指望调阈值提升精度：调高会先筛掉正确答案，噪声照样进得来。
阈值负责挡住"公司年假有几天"这种完全离题的问题（实测返回空），
文档内部的排序交给 top-k 和模型自己判断。

**一个例外：记忆的偏好类不做向量筛选**。记忆条数超过阈值后，事实类按语义相关度召回，
但"希望回答简洁"这类偏好永远全量注入——它和"什么是虚拟 DOM"在语义上没有半点相似性，
按相似度一定被筛掉，P1 那个"偏好不生效"的问题就会原地复活。

### 9. 前端状态分层

| 状态 | 归属 | 说明 |
|---|---|---|
| 登录态 | `useAuth`（模块级单例） | 任何组件拿到的都是同一份 |
| 会话列表 / currentId | `useConversations`（单例） | 侧边栏和聊天窗口共享 |
| 长期记忆 | `useMemories`（单例） | 头部角标和记忆面板共享 |
| 消息 / 发送状态 | `useChat`（App 持有） | 跟着当前会话切换 |

不引 Pinia 的原因：模块作用域 + ref 已经够用，少一层概念。

### 10. 换向量模型

只改 `backend/.env` 里这几行，代码不用动：

```dotenv
EMBED_API_KEY=...
EMBED_BASE_URL=...        # 带 /v1 或 /v4，别漏
EMBED_MODEL=...
EMBED_DIM=1024            # 必须和模型实际输出一致
EMBED_DIMENSIONS=0        # 模型支持"按需输出维度"时填目标维度，否则 0
```

三家可选项：

| 服务商 | BASE_URL | MODEL | DIM | DIMENSIONS |
|---|---|---|---|---|
| 硅基流动 | `https://api.siliconflow.cn/v1` | `BAAI/bge-m3` | 1024 | 0 |
| 智谱 BigModel | `https://open.bigmodel.cn/api/paas/v4` | `embedding-3` | 1024 | 1024 |
| 阿里百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `text-embedding-v3` | 1024 | 1024 |

**换完不用手工改表。** 启动时检测到维度不一致会自动处理：拆索引 → 清空旧向量 →
改列类型 → 重建索引 → 把受影响的文档打回 `pending` 等待重索引。
旧向量直接丢弃而不是转换，因为 1024 维和 2048 维之间没有有意义的换算关系。

**但 `RAG_MIN_SCORE` 必须重新校准**，它是模型相关的，跨模型照抄一定出问题。
做法：准备 6 组"明确相关"和 6 组"完全无关"的句子对，把余弦相似度打出来看两个区间的分界，
阈值放中间偏下一点。详见 `config.py` 里 `RAG_MIN_SCORE` 的注释。

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

**Q: 上传文档报 503？**
A: 没配向量模型。在 `backend/.env` 填 `EMBED_API_KEY` 后重启后端。这个检查放在上传入口是故意的：
否则你会看到"上传成功 → 一直 pending → failed"，错误藏在文档的 error 字段里，很难联想到是没配 Key。

**Q: 上传完一直显示"处理中"？**
A: 看后端日志。常见原因是 embedding 接口超时或限流（免费模型有 RPM 限制）。
文档超过 5MB 或正文超过 20 万字符也会被截断或拒绝，这两种情况前端会直接给出原因。

**Q: 文档是 ready 的，但 AI 说资料里没有？**
A: 三个排查方向：① `RAG_MIN_SCORE` 默认 0.45，问法和原文差太远时会被阈值筛掉，
可以临时调低试试（但别低于 0.4，会把离题内容也放进来）；
② 看工具的 docstring——模型可能压根没决定调用它，换个问法（"我上传的资料里有没有提到 xx"）通常就触发了；
③ 切片用的是原文，如果 PDF 是扫描件，本来就抽不出文字。

**Q: 上传后文档失败，提示"账户余额或免费额度不足（HTTP 402）"？**
A: 向量服务商的账户额度问题，不是代码问题。注意：页面上标注"免费"的模型
**同样受账户额度闸门约束**，余额为 0 时一次都调不了，而且换 API Key 也没用——
限制在账户上，不在 Key 上。实测连免费的小模型对话接口也返回同一个 402。
解决方式是给该账户充值或完成实名认证。判断方法：直接调一次 `/embeddings` 和一个免费 `/chat/completions`，
两个都 402 就说明是账户级问题，不用再怀疑模型名或请求格式。

**Q: 报 `expected 1024 dimensions, not 2048`？**
A: 以前需要手工改表，现在不用了。启动时检测到 `EMBED_DIM` 与数据库列维度不一致，
会自动拆索引、清空旧向量、改列类型、重建索引，并把受影响文档打回 `pending`。
注意旧向量会被**丢弃**而不是转换——换了模型，向量本来就必须重算。
文档需要点一次"重新索引"才能恢复检索。

## 下一步

- [ ] 混合检索：向量召回 + 关键词（BM25）并用 RRF 融合。纯向量对"精确匹配专有名词"弱，
      比如问"简历里的 P7 是什么"，向量可能召回一堆语义相近但不含该编号的段落
- [ ] 检索结果做重排序（bge-reranker），先粗排取 20 条再精排取 5 条
- [ ] 引用可点击：现在回答里只有文件名和段号，理想状态是点一下能跳到原文位置
- [ ] 记忆提炼：把同类记忆合并去重（现在"在杭州"和"在杭州工作"会同时存在）
- [ ] 会话标题用模型生成（现在是首条消息截断）
- [ ] 部署：Docker Compose 打包前后端
