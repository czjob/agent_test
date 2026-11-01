"""
FastAPI + 手写 Agent 循环 流式对话后端

启动: python -m uvicorn main:app --reload --port 8000
      （--reload 表示代码改动后自动重启，开发时用）

核心设计（为什么不用预置 agent）:
  langchain 1.x 的预置 agent（create_agent）内部用 ainvoke 调模型：
    - astream_events 拿不到 token 级分片（模型结果整条推送）
    - on_chat_model_stream / on_chat_model_end 事件也不触发
  做不到打字机效果。所以这里手写经典的 Agent 循环：

      while True:
          1. model.astream(messages)   # 逐 token 流式生成，边收边推给前端
          2. AIMessage 带 tool_calls -> 执行工具，ToolMessage 追加进 messages，继续循环
          3. 不带 tool_calls -> 就是最终回答，结束

  会话记忆：进程内 dict 按 session_id 存完整消息历史
  （和 langgraph 的 InMemorySaver 一样：重启即丢，生产环境换 Redis/DB）。
"""

# ====================== 标准库导入 ======================
import ast          # 把表达式字符串解析成 AST（抽象语法树），用于安全求值，避免直接 eval
import asyncio      # 异步 sleep：控制 token 推送节奏（打字机效果）
import json         # 把 Python 对象序列化成 JSON 字符串，SSE 报文里承载数据
import operator     # 提供 + - * / 等运算符的函数形式（operator.add 等），供 AST 节点分发调用
import os           # 读取环境变量（API Key、模型名等），避免敏感信息硬编码
from datetime import datetime  # 获取当前时间，"获取当前时间"工具用它
from typing import AsyncGenerator  # 类型注解：标注异步生成器函数，增强可读性

# ====================== 第三方库导入 ======================
from fastapi import FastAPI                          # Web 框架本体
from fastapi.middleware.cors import CORSMiddleware   # 跨域中间件：允许浏览器从其他端口访问本服务
from fastapi.responses import StreamingResponse      # 流式响应：边生成边推送，不等全部生成完
from pydantic import BaseModel                       # 数据模型基类：用于声明和校验请求体结构

from dotenv import load_dotenv                       # 加载 backend/.env 中的环境变量（密钥本地存放，不入库）
from langchain_openai import ChatOpenAI              # OpenAI 兼容协议的聊天模型封装（DeepSeek 也兼容）
from langchain_core.tools import tool                # 装饰器：把普通 Python 函数包装成大模型可调用的工具
# langchain_core.messages：对话消息的类型，手写循环直接和它们打交道
from langchain_core.messages import (
    AIMessage,        # 模型的回复（content 文本 + tool_calls 工具调用请求）
    BaseMessage,      # 上面几种消息的基类，用来给"消息列表"做类型标注
    HumanMessage,     # 用户说的话
    SystemMessage,    # 系统提示（人设/规则），每轮请求都放在最前面
    ToolMessage,      # 工具执行结果，必须回传给模型它才能"看到"
)

# ====================== 初始化 FastAPI ======================
app = FastAPI(title="LangChain Agent Demo")

# 允许前端跨域（开发环境用 *，生产环境改成你的前端域名，如 http://your-site.com）
# 机制说明：浏览器有同源策略，前端 (localhost:5173) 请求后端 (localhost:8000)
# 属于跨域，后端必须显式返回 CORS 响应头，浏览器才会放行。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # 允许所有来源的域名；* 表示不限制
    allow_credentials=True,     # 允许携带 Cookie 等凭证。注意：与 * 搭配时 Starlette
                                # 会在响应里回显具体 Origin（而非 *）并加 Vary: Origin
    allow_methods=["*"],        # 允许所有 HTTP 方法（GET/POST/...）
    allow_headers=["*"],        # 允许所有请求头（Content-Type、Authorization 等）
)

# ====================== 初始化大模型 ======================
# 国内模型可改 base_url，比如 DeepSeek: https://api.deepseek.com/v1
# 模型名和密钥建议通过环境变量注入，避免硬编码泄露
# （os.getenv("环境变量名", "默认值")：优先读环境变量，读不到就用默认值）
load_dotenv()  # 读取同目录下的 .env 文件注入环境变量（.env 已被 .gitignore 排除，不会上传）

llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),        # 模型名称
    temperature=0,                                        # 温度 0 = 输出最确定，适合工具调用场景
    api_key=os.getenv("DEEPSEEK_API_KEY"),                # API 密钥：只从环境变量/.env 读取，绝不硬编码进代码
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),             # API 地址，换模型只改这里
)

# ====================== 定义工具 ======================
# 安全四则运算求值器：只解析数字和 + - * / // % ** ，杜绝 eval 注入
# 背景：eval("__import__('os').system('rm -rf /')") 这种字符串能直接执行任意代码，
# 所以这里不用 eval，而是用 ast 把表达式拆成语法树，只放行白名单内的节点类型。
_BIN_OPS = {
    ast.Add: operator.add,        # a + b
    ast.Sub: operator.sub,        # a - b
    ast.Mult: operator.mul,       # a * b
    ast.Div: operator.truediv,    # a / b（真除法，结果是小数）
    ast.FloorDiv: operator.floordiv,  # a // b（整除）
    ast.Mod: operator.mod,        # a % b（取余）
    ast.Pow: operator.pow,        # a ** b（幂运算）
}


def _safe_eval(node) -> float:
    """
    递归遍历 AST 并求值。
    每种语法结构对应一种节点类型，只处理白名单内的类型，其他一律报错：
      - ast.Expression : 整棵树的根节点（ast.parse(mode="eval") 的返回值）
      - ast.Constant   : 常量（数字字面量）
      - ast.BinOp      : 二元运算（左 操作符 右）
      - ast.UnaryOp    : 一元运算（+x / -x）
    """
    if isinstance(node, ast.Expression):
        # 根节点：剥掉外壳，递归处理里面的 body
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        # 数字常量：直接返回值（排除字符串常量，杜绝 "abc" 这类输入）
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        # 二元运算：先递归求左右两边的值，再按操作符类型查表计算
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        # 防御 9**9**9 这种能把 CPU 打满的指数爆炸
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("指数过大")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        # 一元运算：-5 → 取相反数；+5 → 原样返回
        value = _safe_eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    # 其他任何节点类型（函数调用、属性访问、名字引用……）一律拒绝
    raise ValueError(f"不支持的表达式: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """
    数学计算器，仅支持加减乘除四则运算。
    参数 expression 是合法的数学表达式字符串，例如 "256 * 32"

    【重点】这个 docstring 不是写给人看的，是写给大模型的"工具说明书"：
    模型靠函数名 + docstring + 参数类型注解 来决定"要不要调用这个工具、
    参数怎么传"。docstring 写得清不清晰，直接决定工具调用成功率。
    """
    try:
        # ast.parse(mode="eval")：把表达式字符串解析成 AST（不执行）
        result = _safe_eval(ast.parse(expression, mode="eval"))
        return f"计算结果：{result}"
    except Exception as e:
        # 工具内部出错不要抛异常中断对话，把错误信息作为字符串返回给模型，
        # 模型会自己组织语言告诉用户"算错了"，体验更自然
        return f"计算错误：{str(e)}"


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    now = datetime.now()
    return now.strftime("当前时间：%Y年%m月%d日 %H:%M:%S")


# 工具列表
tools = [calculate, get_current_time]

# 工具名 -> 工具实例 的映射：循环里按模型给出的工具名，找到对应函数执行
_TOOLS_BY_NAME = {t.name: t for t in tools}

# ====================== 把工具绑定到模型 ======================
# bind_tools：把工具的"说明书"（函数名 / docstring / 参数 JSON Schema）
# 注入到每次模型请求里，模型就能在需要时返回 tool_calls
# （要调哪个工具、传什么参数、调用 id 是什么）。
SYSTEM_PROMPT = "你是智能助手，必要时调用工具完成任务，回答简洁准确。"

# model 是"绑定了工具"的模型：之后每次 astream/ainvoke 都会带上这些工具定义
model = llm.bind_tools(tools)

# ====================== 会话记忆 ======================
# 结构: {session_id: [HumanMessage, AIMessage, ToolMessage, ...]}
# 每次请求带上该会话的历史消息，模型就能"记住"之前聊过什么（多轮对话）。
# 注意: 存在进程内存里，服务重启 / 多 worker 部署都会丢，生产环境换 Redis/数据库。
_histories: dict[str, list[BaseMessage]] = {}

# 历史长度上限：只保留最近 N 条，防止上下文无限膨胀
# （token 成本越来越高，最终超出模型上下文窗口报错）
_MAX_HISTORY_MESSAGES = 40

# Agent 循环的最大轮数：模型"调工具 -> 看结果 -> 再调工具"可能来回多轮，
# 这个上限防止模型陷入死循环（每轮 = 一次模型调用）
_MAX_ROUNDS = 8

# 打字机节奏：每个 token 推送之间的最小间隔（秒）。
# 模型生成快时几毫秒就吐一个 token，短回答会"嗖"地一下出现，看不出流式效果；
# 加这个间隔保证肉眼可见的逐字节奏。设为 0 表示完全跟随模型速度，不做限制。
_TOKEN_MIN_INTERVAL = 0.03


def _get_history(session_id: str) -> list[BaseMessage]:
    """取某个会话的消息历史；首次访问会创建空列表。"""
    return _histories.setdefault(session_id, [])


# ====================== 请求模型 ======================
class ChatRequest(BaseModel):
    """
    前端 POST /chat/stream 的请求体结构。
    pydantic 的 BaseModel 让 FastAPI 自动做三件事：
    1. 校验 JSON 字段（缺失/类型不对会返回 422，不会进入业务代码）
    2. 把 JSON 自动转成这个类的实例（req.session_id / req.input 直接取值）
    3. 生成 API 文档（/docs 页面能看到结构）
    """
    session_id: str = "default"  # 会话 ID：同一会话的多轮消息靠它串起来；不传则用默认值
    input: str                   # 用户本次输入的消息内容


# ====================== 流式对话接口（SSE） ======================
@app.post("/chat/stream")  # 路由装饰器：把下面这个函数注册为 POST /chat/stream 接口
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """
    流式输出接口，使用 SSE (Server-Sent Events) 协议。
    前端通过 fetch + ReadableStream 逐块接收。

    SSE 协议要点：
      - 基于普通 HTTP 长连接，响应体是 text/event-stream 类型
      - 每条消息格式为 "data: {内容}\n\n"（两个换行才算一条消息结束）
      - 服务端可以随时往里写新数据，浏览器边收边解析，不需要 WebSocket
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        """
        异步生成器：每 yield 一个字符串，FastAPI 就立刻把它写进 HTTP 响应流。
        （对比：普通函数要 return 整个字符串才会发给客户端，做不到"边生成边推送"）
        """
        # 拼出本次请求的完整消息序列：
        #   系统提示（每轮重新拼，不存入历史，避免重复堆积）
        # + 该会话的历史消息（多轮记忆的关键）
        # + 本轮用户输入
        messages: list[BaseMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            *_get_history(req.session_id),
            HumanMessage(content=req.input),
        ]

        try:
            # ====================== Agent 主循环 ======================
            # 每一轮：问模型 -> 要么直接回答（结束），要么要求调工具 -> 执行 -> 再问
            got_final_answer = False
            for _round in range(_MAX_ROUNDS):
                # 1. 流式调用模型：astream 逐 token 返回 AIMessageChunk（小分片）
                full_msg = None
                async for chunk in model.astream(messages):
                    content = chunk.content  # 本次分片的文本增量（一个词/几个字）
                    if content:
                        # 控制推送节奏，保证打字机效果肉眼可见（模型太快时兜底）
                        if _TOKEN_MIN_INTERVAL > 0:
                            await asyncio.sleep(_TOKEN_MIN_INTERVAL)
                        # 每收到一个分片就立刻 yield 推给前端 -> 打字机效果
                        # （注意：DeepSeek 推理阶段的 reasoning 在 additional_kwargs 里，
                        #   content 为空，不会误推；只有正式回答才会推）
                        yield f"data: {json.dumps({'type': 'token', 'content': content}, ensure_ascii=False)}\n\n"
                    # 分片累加成完整消息：AIMessageChunk 的 + 运算会自动把
                    # tool_call_chunks 按 index 拼接，最终得到完整的 tool_calls
                    full_msg = chunk if full_msg is None else full_msg + chunk

                # 防御：模型一条分片都没返回（异常情况），直接结束
                if full_msg is None:
                    break

                # 2. 把模型的完整回复追加进消息序列——下一步无论执行工具还是
                #    结束，都需要让模型"看到"自己这一轮说了什么
                messages.append(full_msg)

                # 3. 没有工具调用 -> 这就是最终回答，跳出循环
                if not full_msg.tool_calls:
                    got_final_answer = True
                    break

                # 4. 有工具调用 -> 逐个执行，结果作为 ToolMessage 追加进消息序列
                #    （ToolMessage 必须带 tool_call_id，模型靠它把结果和请求对应上）
                for tc in full_msg.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    tool_call_id = tc["id"]

                    # 先告诉前端"正在调工具"（前端会显示橙色状态行）
                    yield f"data: {json.dumps({'type': 'tool_start', 'content': f'🔧 正在调用工具：{tool_name}'}, ensure_ascii=False)}\n\n"

                    tool = _TOOLS_BY_NAME.get(tool_name)
                    if tool is None:
                        # 模型幻觉出不存在的工具：不崩溃，把错误作为结果回传给模型，
                        # 它通常会自己纠正（换工具或直接回答）
                        result = f"错误：工具 {tool_name} 不存在"
                    else:
                        # .invoke(参数字典) 执行工具；工具内部已 try 兜底，不会抛异常
                        # （同步函数很快，直接在异步里调；耗时工具应放线程池）
                        result = str(tool.invoke(tool_args))

                    # 再把工具结果推给前端展示
                    yield f"data: {json.dumps({'type': 'tool_end', 'content': f'✅ 工具返回：{result}'}, ensure_ascii=False)}\n\n"
                    messages.append(
                        ToolMessage(content=result, tool_call_id=tool_call_id, name=tool_name)
                    )
                # 5. 继续下一轮循环：模型看到工具结果后，生成最终总结

            if got_final_answer:
                # ====================== 保存历史 ======================
                # messages[0] 是系统提示不存；只存对话消息（用户/AI/工具），
                # 并截取最近 _MAX_HISTORY_MESSAGES 条
                _histories[req.session_id] = messages[1:][-_MAX_HISTORY_MESSAGES:]
                # 流结束标记：告诉前端"这条消息结束了"，可以解除 loading 状态
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            else:
                # 轮数用完还没得到最终回答（或模型无响应）：以 error 事件告知前端
                yield f"data: {json.dumps({'type': 'error', 'content': '生成失败：模型未返回有效内容，或工具调用轮数超出上限'}, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 兜底：链路里任何异常都以 SSE error 事件推给前端显示，
            # 而不是让连接直接断开（HTTP 头已经发出去了，断连前端只能看到网络错误）
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    # StreamingResponse 接收异步生成器，边收边发；
    # media_type 告诉浏览器"这是 SSE 流，别等它结束"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",   # 禁止中间层缓存，保证实时性
            "Connection": "keep-alive",    # 保持长连接
        },
    )


# ====================== 健康检查 ======================
@app.get("/")
async def root():
    """探活接口：启动后 curl http://localhost:8000/ 能通就说明服务起来了。"""
    return {"status": "ok", "message": "Agent 后端服务运行中"}
