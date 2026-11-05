"""
Agent 可调用的工具。

【给后来者的说明】
@tool 装饰的函数，docstring 不是写给人看的，是写给模型看的"工具说明书"。
模型靠 函数名 + docstring + 参数类型注解 来决定要不要调用、参数怎么传。
docstring 写得含糊，工具调用率会直接掉下来。

P2 加了 search_knowledge_base：
  它需要知道"当前是哪个用户在查"，但 user_id 绝对不能作为工具参数传——
  参数是模型生成的，模型能被提示注入诱导，把 user_id 换成别人的。
  所以用 ContextVar 在请求进入时由服务端注入，模型的输入里根本没有这个字段。
  这也是把越权防线放在 SQL 而不是 Python 里的延续：能不让模型碰的东西就不让它碰。
"""

import ast
import logging
import operator
import uuid
from contextvars import ContextVar, Token
from datetime import datetime

from langchain_core.tools import tool

from config import settings
from db import SessionLocal
from embeddings import embeddings
from services import knowledge as kb_service

logger = logging.getLogger(__name__)

# 当前请求的用户 id。默认 None，表示"不在对话上下文里"（比如直接调用工具做测试）。
_current_user_id: ContextVar[uuid.UUID | None] = ContextVar("current_user_id", default=None)


def bind_request_context(user_id: uuid.UUID) -> Token:
    """
    在开始处理一轮对话时调用，把用户身份注入当前协程上下文。

    返回的 token 必须原样交给 reset_request_context：
    同一个进程会并发处理多个用户的请求，协程之间靠 contextvar 隔离，
    set 之后不 reset 会让上下文"泄漏"到后续任务里（asyncio 会复制父任务的上下文）。
    """
    return _current_user_id.set(user_id)


def reset_request_context(token: Token) -> None:
    """恢复进入前的上下文。放在 finally 里调用。"""
    _current_user_id.reset(token)


# 安全四则运算求值器：只放行数字和 + - * / // % **，杜绝 eval 注入。
# 背景：eval("__import__('os').system('rm -rf /')") 能直接执行任意代码，
# 所以这里用 ast 把表达式拆成语法树，只处理白名单内的节点类型。
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _safe_eval(node) -> float:
    """递归遍历 AST 并求值，只处理白名单节点类型，其余一律拒绝。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        # 排除字符串常量，杜绝 "abc" 这类输入
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        # 防御 9**9**9 这种能把 CPU 打满的指数爆炸
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("指数过大")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _safe_eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    raise ValueError(f"不支持的表达式: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """
    数学计算器，仅支持加减乘除四则运算。
    参数 expression 是合法的数学表达式字符串，例如 "256 * 32"。
    """
    try:
        result = _safe_eval(ast.parse(expression, mode="eval"))
        return f"计算结果：{result}"
    except Exception as e:
        # 工具内部出错不要抛异常中断对话，把错误信息作为字符串返回，
        # 模型会自己组织语言告诉用户，体验更自然。
        return f"计算错误：{str(e)}"


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    return datetime.now().strftime("当前时间：%Y年%m月%d日 %H:%M:%S")


def _format_hits(hits: list) -> str:
    """
    把检索结果拼成给模型看的文本。

    格式里带文件名和段号，是为了让模型能说"根据《xx》第 3 段"——
    用户看到引用出处才会相信这个回答不是编的。
    结尾那句"资料不足就直说"是防幻觉的关键：不写这句，模型倾向于
    把检索到的无关片段硬凑成答案，比"没找到"更难被发现。
    """
    lines = [f"在知识库中找到 {len(hits)} 段相关内容（按相似度从高到低）："]
    budget = settings.RAG_TOOL_MAX_CHARS

    for index, hit in enumerate(hits, start=1):
        header = f"\n【{index}】《{hit.filename}》第 {hit.seq + 1} 段（相似度 {hit.score:.2f}）"
        # 单段也要截断：一段可能就几百字，k 段疊加容易把上下文撑满
        body = hit.content.strip()
        piece = f"{header}\n{body}"
        if len(piece) > budget:
            piece = piece[:budget] + "…（已截断）"
        budget -= len(piece)
        lines.append(piece)
        if budget <= 0:
            break

    lines.append(
        "\n以上是从用户上传的资料里检索到的原文片段。"
        "回答时以这些内容为准，可以注明出处；"
        "如果片段不足以回答问题，直接说明资料里没有相关内容，不要凭猜测补充。"
    )
    return "\n".join(lines)


@tool
async def search_knowledge_base(query: str) -> str:
    """
    在用户上传的知识库文档里做语义检索，返回最相关的原文片段。

    什么时候必须调用：
      问题涉及"我上传的资料 / 我的简历 / 这份文档 / 我之前给的文件"里的内容，
      或者需要引用具体资料原文才能回答时，先调用这个工具再回答。

    什么时候不要调用：
      闲聊、常识问题、数学计算、不需要查资料就能回答的问题。

    参数 query：用完整的自然语言描述你要找什么（例如"候选人的工作经历和项目经验"），
    不要只传几个关键词，完整句子的检索效果更好。
    """
    user_id = _current_user_id.get()
    if user_id is None:
        # 理论上不会发生：只有 /chat/stream 会触发工具调用，那里一定绑了上下文
        return "检索失败：当前对话没有用户上下文，无法访问知识库。"

    if not embeddings.configured:
        return "知识库不可用：服务端没有配置向量模型。请直接回答用户，并说明这个环境暂时用不了资料检索。"

    if not embeddings.available:
        # 冷却期。不要抛异常——抛了会让整个 Agent 循环报错，
        # 而"这次查不了资料"远没有"对话直接失败"严重
        return "知识库暂时无法访问（向量服务异常）。请基于已有信息回答，并说明这次没能查到资料。"

    try:
        async with SessionLocal() as db:
            # 先看用户到底有没有文档，能把"空知识库"和"没检索到"区分开，
            # 否则模型会对一个从没上传过资料的用户说"资料里没提到"，很奇怪
            total = await kb_service.count_ready_documents(db, user_id)
            if total == 0:
                return (
                    "该用户还没有上传任何可用文档，知识库是空的。"
                    "请直接回答，并在合适的时候提示用户可以上传资料再问。"
                )
            hits = await kb_service.search(db, user_id, query)
    except Exception:
        logger.exception("知识库检索失败，user_id=%s", user_id)
        return "知识库检索出错，请基于已有信息回答，并说明这次没能查到资料。"

    if not hits:
        return (
            "知识库里没有找到与这个问题相关的内容。"
            "请直接回答用户，并明确说明资料里没有提到这一点，不要编造。"
        )

    return _format_hits(hits)


# 工具列表。新增工具只需要往这里加，agent.py 会自动 bind 和分发。
tools = [calculate, get_current_time, search_knowledge_base]

# 工具名 -> 工具实例。循环里按模型给出的工具名查表执行。
TOOLS_BY_NAME = {t.name: t for t in tools}
