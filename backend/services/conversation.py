"""
会话与消息的数据访问层。

所有函数都要求传入 user_id（或已校验归属的 Conversation 对象），
这是防越权的第一道闸：任何"按 id 查"都必须带上 user_id 条件，
不能先按 id 查出来再在 Python 里判断——那样一旦某个接口忘了判断就是漏洞。
"""

import uuid
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import DEFAULT_CONVERSATION_TITLE, Conversation, Message

# 标题最大字数。中文标题按字符数截断即可，不需要按 token 算。
_TITLE_MAX_CHARS = 20


# ====================== 会话 ======================
async def create_conversation(
    db: AsyncSession, user_id: uuid.UUID, title: str | None = None
) -> Conversation:
    conversation = Conversation(
        user_id=user_id,
        title=(title or DEFAULT_CONVERSATION_TITLE).strip()[:_TITLE_MAX_CHARS]
        or DEFAULT_CONVERSATION_TITLE,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def list_conversations(
    db: AsyncSession, user_id: uuid.UUID, days: int | None = None
) -> list[Conversation]:
    """
    侧边栏列表。

    过滤条件三个缺一不可：
      user_id   —— 只能看到自己的
      deleted_at is null —— 排除用户删掉的
      archived_at is null —— 排除系统归档的（超过 30 天无更新）
    另外按时间窗再过一道：只取最近 N 天有更新的。
    """
    window_days = days if days is not None else settings.CONVERSATION_LIST_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    stmt = (
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
            Conversation.archived_at.is_(None),
            Conversation.updated_at >= cutoff,
        )
        .order_by(Conversation.updated_at.desc())
    )
    return list((await db.scalars(stmt)).all())


async def get_conversation(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    """按 id 取会话，同时校验归属。不属于该用户时返回 None（对外表现为 404，不泄露存在性）。"""
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
        Conversation.deleted_at.is_(None),
    )
    return await db.scalar(stmt)


async def rename_conversation(db: AsyncSession, conversation: Conversation, title: str) -> Conversation:
    conversation.title = title.strip()[:_TITLE_MAX_CHARS] or DEFAULT_CONVERSATION_TITLE
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def soft_delete_conversation(db: AsyncSession, conversation: Conversation) -> None:
    """软删除：只标记时间，数据保留。误删可以从库里恢复。"""
    conversation.deleted_at = datetime.now(timezone.utc)
    await db.commit()


async def clear_messages(db: AsyncSession, conversation: Conversation) -> None:
    """
    清空某个会话的消息，会话本身保留。

    摘要必须一起清掉：summary 是对"已被删掉的那些消息"的描述，
    留着它，模型下一轮就会拿一段不存在的历史回答问题，比没有摘要更糟。
    """
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    conversation.message_count = 0
    conversation.summary = None
    conversation.summary_until_seq = 0
    conversation.updated_at = datetime.now(timezone.utc)
    await db.commit()


# ====================== 消息 ======================
async def append_message(
    db: AsyncSession,
    conversation: Conversation,
    role: str,
    content: str,
    *,
    tool_calls: list | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> Message:
    """
    追加一条消息。

    seq 用 max(seq)+1 计算。并发写同一会话时理论上会撞唯一约束，但前端在流式生成期间
    会禁用输入框，同一会话不会并发写；真撞了也只是抛异常，不会写坏数据。
    """
    next_seq = (
        await db.scalar(
            select(func.coalesce(func.max(Message.seq), 0)).where(
                Message.conversation_id == conversation.id
            )
        )
    ) + 1

    message = Message(
        conversation_id=conversation.id,
        seq=next_seq,
        role=role,
        content=content or "",
        tool_calls=tool_calls,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
    db.add(message)

    # 冗余计数 +1，同时 onupdate 会刷新 updated_at（侧边栏排序依赖它）
    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(message)
    return message


async def load_messages(db: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    """取出会话的全部消息，按 seq 升序。前端渲染历史用这个。"""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq.asc())
    )
    return list((await db.scalars(stmt)).all())


async def load_recent_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    limit: int | None = None,
    *,
    since_seq: int = 0,
) -> list[Message]:
    """
    取最近 N 条消息，返回时仍是升序。

    实现是"先倒序取 N 条再反转"，避免 ORDER BY 正序 + 大 offset 的全表扫描。

    since_seq：只取 seq 大于它的消息。L2 压缩后，这条线以上的原文已经被摘要吸收，
    不需要也不应该再塞进 prompt（会重复占 token）。
    """
    window = limit if limit is not None else settings.HISTORY_WINDOW

    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if since_seq:
        stmt = stmt.where(Message.seq > since_seq)

    stmt = stmt.order_by(Message.seq.desc()).limit(window)
    rows = list((await db.scalars(stmt)).all())
    rows.reverse()
    return rows


async def load_context(
    db: AsyncSession, conversation: Conversation
) -> tuple[str | None, list[Message]]:
    """
    组装喂给模型的上下文，返回 (摘要, 未压缩区的消息)。

    三种情况：
      1. 没有摘要（summary_until_seq = 0）→ 退化成"最近 40 条原文"，即 P0 的行为
      2. 有摘要 → 摘要 + 摘要线之后的原文
      3. 摘要线之后的原文超过窗口 → 仍然按窗口截断，防止极端情况撑爆 prompt
         （正常情况下压缩任务会跟上，很少走到这里）

    注意这里不会触发压缩——压缩是后台任务（services/summary.py），
    本函数只负责"读当前最好的上下文"，保证流式响应能立刻开始。
    """
    since_seq = conversation.summary_until_seq or 0
    rows = await load_recent_messages(db, conversation.id, since_seq=since_seq)
    return conversation.summary, rows


def to_langchain_messages(rows: list[Message]) -> list[BaseMessage]:
    """
    把数据库里的消息行还原成 LangChain 的消息对象，喂给模型。

    两个细节容易踩坑：
    1. 截断窗口可能从一条 tool 消息开始。孤立的 ToolMessage 没有对应的
       AIMessage.tool_calls，OpenAI 兼容接口会直接报 400。所以要把开头的 tool 消息丢掉。
    2. AIMessage 必须带上原来的 tool_calls，否则后续的 ToolMessage 对不上 id，同样报错。
    """
    # 丢掉开头连续的 tool 消息（窗口截断产生的孤儿）
    start = 0
    while start < len(rows) and rows[start].role == "tool":
        start += 1

    result: list[BaseMessage] = []
    for row in rows[start:]:
        if row.role == "user":
            result.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            result.append(AIMessage(content=row.content, tool_calls=row.tool_calls or []))
        elif row.role == "tool":
            result.append(
                ToolMessage(
                    content=row.content,
                    tool_call_id=row.tool_call_id or "",
                    name=row.tool_name or "",
                )
            )
        # 未知 role 静默跳过，避免脏数据把整轮对话打挂
    return result


def make_title(text: str) -> str:
    """
    用首条用户消息生成标题。

    这里刻意不调 LLM：省一次调用、少一份延迟。P1 做记忆时如果觉得不满意，
    可以再换成"异步调模型总结成 6~12 字"，接口不用改。
    """
    cleaned = " ".join(text.split())  # 合并换行和多余空格
    if len(cleaned) <= _TITLE_MAX_CHARS:
        return cleaned or DEFAULT_CONVERSATION_TITLE
    return cleaned[:_TITLE_MAX_CHARS] + "…"


# ====================== 生命周期清理 ======================
async def run_retention_cleanup(db: AsyncSession) -> tuple[int, int]:
    """
    两阶段清理，返回 (本次归档数, 本次物理删除数)。

    阶段一：超过 ARCHIVE_AFTER_DAYS 天没更新的会话 → 打 archived_at 标记
            （列表里消失，但数据还在，误判了能恢复）
    阶段二：归档又超过 PURGE_AFTER_DAYS 天的会话 → 物理删除
            （messages 通过外键 ON DELETE CASCADE 一起删掉）
    """
    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=settings.ARCHIVE_AFTER_DAYS)
    purge_cutoff = now - timedelta(days=settings.PURGE_AFTER_DAYS)

    archived = await db.execute(
        update(Conversation)
        .where(
            Conversation.archived_at.is_(None),
            Conversation.deleted_at.is_(None),
            Conversation.updated_at < archive_cutoff,
        )
        .values(archived_at=now)
    )

    purged = await db.execute(
        delete(Conversation).where(
            Conversation.archived_at.is_not(None),
            Conversation.archived_at < purge_cutoff,
        )
    )

    await db.commit()
    return archived.rowcount or 0, purged.rowcount or 0
