"""
L3 长期记忆：从对话里抽取"关于用户本人"的稳定信息，跨会话生效。

和 L2 摘要的分工：
  L2 summary —— 一个会话内部的压缩，只在这个会话里生效，回答"我们刚才聊到哪了"
  L3 memory  —— 跨会话的画像，回答"这个人是谁、他喜欢什么"，所有会话共享

为什么用"每轮异步抽取"而不是"定期批量扫描"：
  批量扫描需要额外的定时任务，还要判断哪些会话是新的；
  每轮结束抽一次，逻辑简单，且用户刚说完的话信息密度最高。
  代价是每轮多一次模型调用——放在流式结束后跑，用户完全感知不到。

去重靠数据库唯一约束（user_id, content）而不是应用层查一遍：
  抽取是后台任务，同一个用户可能有两轮对话同时在抽，
  先查再插会撞车（查的时候都没有，插的时候冲突）。
  ON CONFLICT 交给数据库处理才是原子的。
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import SessionLocal
from llm import llm
from models import MEMORY_KINDS, Memory, Message

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """从下面这轮对话中提取值得长期记住的、关于用户本人的信息。

只提取这三类：
- profile：身份信息（姓名、职业、所在城市、语言习惯等）
- preference：稳定的偏好与要求（喜欢什么、讨厌什么、希望你怎样回答）
- fact：长期有效的事实（项目背景、正在做的事、重要日期、约定）

不要提取：
- 一次性的问题内容、临时计算、与用户本人无关的闲聊
- 已经出现在「已知记忆」里的信息

已知记忆（包括用户主动删除过的，都不要重复输出）：
{known}

本轮对话：
{transcript}

输出 JSON（只输出 JSON，不要任何解释）：
{{"memories": [{{"kind": "preference", "content": "希望回答简洁，不要长篇大论", "importance": 0.8}}]}}

importance 取 0~1：0.9 以上是长期稳定的核心信息，0.5 左右是一般背景，0.3 以下不太重要。
content 必须写成一句脱离上下文也说得通的话，不要用"他""这个""刚才说的"这类指代。
如果没有任何值得记住的信息，输出 {{"memories": []}}。"""

# 抽取时最多把多少条已有记忆交给模型参考（防止 prompt 无限膨胀）
_KNOWN_LIMIT = 50


def _clip(text: str, limit: int) -> str:
    """按字符截断，超长加省略号。中文按字符数算比按 token 算更直观。"""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _parse_json(raw: str) -> dict:
    """
    从模型输出里抠出 JSON。

    即使开了 json_object 模式，模型偶尔还是会套一层 ```json 围栏，
    所以这里做一次清洗再解析，解析失败就当作"没有新记忆"，不让流程崩掉。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 再试一次：抠出第一个 { 到最后一个 } 之间的内容
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            logger.warning("记忆抽取返回的不是合法 JSON：%s", _clip(text, 200))
            return {}

    return parsed if isinstance(parsed, dict) else {}


def _render_turn(rows: list[Message]) -> str:
    """把本轮消息渲染成抽取用的对话文本。"""
    lines: list[str] = []
    budget = settings.MEMORY_SOURCE_CLIP
    for row in rows:
        content = _clip(row.content, 400)
        if not content:
            continue
        if row.role == "tool":
            # 工具结果对"了解用户"几乎没用，只留一句
            continue
        speaker = "用户" if row.role == "user" else "助手"
        line = f"{speaker}：{content}"
        if budget - len(line) < 0:
            break
        budget -= len(line)
        lines.append(line)
    return "\n".join(lines)


async def _call_extractor(known: list[str], transcript: str) -> list[dict]:
    """调模型做一次抽取，返回清洗后的候选列表。"""
    prompt = EXTRACT_PROMPT.format(
        known="\n".join(f"- {item}" for item in known) if known else "（暂无）",
        transcript=transcript,
    )

    # json_object 模式要求 prompt 里出现 "json" 字样——上面已经有了
    structured = llm.bind(response_format={"type": "json_object"})
    response = await structured.ainvoke(
        [
            SystemMessage(content="你是一个精确的信息抽取器，只输出 JSON。"),
            HumanMessage(content=prompt),
        ]
    )

    data = _parse_json(str(response.content or ""))
    raw_items = data.get("memories")
    if not isinstance(raw_items, list):
        return []

    cleaned: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        content = _clip(str(item.get("content") or ""), settings.MEMORY_MAX_CHARS)
        kind = str(item.get("kind") or "").strip()
        if not content or kind not in MEMORY_KINDS:
            continue

        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        # 夹到 0~1，模型偶尔会给 0.8/1 这种写法或超出范围的值
        importance = min(max(importance, 0.0), 1.0)

        cleaned.append({"kind": kind, "content": content, "importance": importance})

    return cleaned


async def extract_from_turn(
    conversation_id: uuid.UUID, user_id: uuid.UUID, since_seq: int
) -> None:
    """
    后台任务入口：抽取本轮对话里的长期记忆并落库。

    参数 since_seq 是本轮用户消息的 seq，只取它之后的消息——
    比按时间戳过滤更准（同一秒可能有多条消息）。
    """
    if not settings.MEMORY_ENABLED:
        return

    try:
        async with SessionLocal() as db:
            rows = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation_id,
                            Message.seq >= since_seq,
                        )
                        .order_by(Message.seq.asc())
                    )
                ).all()
            )

            # 只有用户说了一句、助手还没回的情况（比如模型报错），没有抽取价值
            if not any(row.role == "user" for row in rows):
                return

            transcript = _render_turn(rows)
            if len(transcript) < 10:
                return

            known = list(
                (
                    await db.scalars(
                        select(Memory.content)
                        .where(Memory.user_id == user_id)
                        .order_by(Memory.created_at.desc())
                        .limit(_KNOWN_LIMIT)
                    )
                ).all()
            )

            candidates = await _call_extractor(known, transcript)

            # 用户在面板里删掉的记忆，不能再被抽回来。
            # 上面把 dismissed 的内容也放进了 prompt 让模型别重复输出，
            # 但模型不一定听话，所以这里再做一次确定性的过滤兜底。
            if candidates:
                dismissed = set(
                    (
                        await db.scalars(
                            select(Memory.content).where(
                                Memory.user_id == user_id,
                                Memory.dismissed_at.is_not(None),
                            )
                        )
                    ).all()
                )
                candidates = [c for c in candidates if c["content"] not in dismissed]

            if not candidates:
                return

            await _upsert_memories(db, user_id, conversation_id, candidates)

            logger.info(
                "会话 %s 记忆抽取完成：候选 %s 条", conversation_id, len(candidates)
            )
    except Exception:
        # 抽取失败不能影响对话，下一轮继续抽就是了
        logger.exception("记忆抽取失败，conversation_id=%s", conversation_id)


async def _upsert_memories(
    db: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    candidates: list[dict],
) -> None:
    """
    批量写入记忆，冲突时取"更高的重要度"。

    ON CONFLICT DO UPDATE 而不是 DO NOTHING：
      同一句话第二次被抽到时，模型给的 importance 可能更高（这次上下文更充分），
      取 max 比固定保留第一次的分数更合理。
    """
    if not candidates:
        return

    values = [
        {
            "user_id": user_id,
            "kind": item["kind"],
            "content": item["content"],
            "importance": item["importance"],
            "source_conversation_id": conversation_id,
        }
        for item in candidates
    ]

    stmt = pg_insert(Memory).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_memories_user_content",
        set_={
            # 保留较大的重要度：记忆被反复提及时说明它更重要
            "importance": func.greatest(Memory.importance, stmt.excluded.importance),
            "kind": stmt.excluded.kind,
        },
    )
    await db.execute(stmt)
    await db.commit()


async def load_for_prompt(db: AsyncSession, user_id: uuid.UUID) -> list[Memory]:
    """
    取出要注入系统提示的记忆，按重要度倒序。

    返回 ORM 对象而不是纯文本：上层要按 kind 分组注入
    （偏好是硬约束，事实只是背景，两者在 prompt 里的措辞不同），
    只给字符串就丢掉了这个信息。这些对象在 session 关闭后依然可读——
    session 配置了 expire_on_commit=False，属性值已经在内存里了。

    顺便刷新 last_used_at：这个字段将来用于"久未使用的记忆自动衰减"，
    现阶段先在记忆面板里按"最近使用"排序，让用户看得出哪些真的在起作用。
    """
    if not settings.MEMORY_ENABLED:
        return []

    rows = list(
        (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.importance >= settings.MEMORY_MIN_IMPORTANCE,
                    Memory.dismissed_at.is_(None),
                )
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(settings.MEMORY_TOP_K)
            )
        ).all()
    )

    if not rows:
        return []

    # 一条 UPDATE 刷新全部命中项，不做逐条更新
    await db.execute(
        update(Memory)
        .where(Memory.id.in_([row.id for row in rows]))
        .values(last_used_at=datetime.now(timezone.utc))
    )
    await db.commit()

    return rows


# ====================== 记忆面板 ======================
async def list_memories(db: AsyncSession, user_id: uuid.UUID) -> list[Memory]:
    """记忆面板列表：按重要度倒序，重要度相同按创建时间倒序。用户删掉的不返回。"""
    stmt = (
        select(Memory)
        .where(Memory.user_id == user_id, Memory.dismissed_at.is_(None))
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
    )
    return list((await db.scalars(stmt)).all())


async def delete_memory(db: AsyncSession, user_id: uuid.UUID, memory_id: int) -> bool:
    """
    删除一条记忆。

    实现是软删除（打 dismissed_at 标记），不是 DELETE：
    抽取任务每轮都在跑，物理删掉之后，下一轮只要又提到同样的内容，
    模型就会把它当成新信息写回来，用户会觉得"删了怎么又出现了"。
    标记 + 抽取时过滤，才是真正删干净。

    注意 where 里也带 user_id：只按 id 删会让人构造别人的 id 来删，
    和会话接口遵循同一条规则——归属校验写在 SQL 里，不写在 Python 里。
    """
    result = await db.execute(
        update(Memory)
        .where(
            Memory.id == memory_id,
            Memory.user_id == user_id,
            Memory.dismissed_at.is_(None),
        )
        .values(dismissed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def clear_memories(db: AsyncSession, user_id: uuid.UUID) -> int:
    """清空该用户的全部记忆（同样是软删除），返回本次清掉的条数。"""
    result = await db.execute(
        update(Memory)
        .where(Memory.user_id == user_id, Memory.dismissed_at.is_(None))
        .values(dismissed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result.rowcount or 0


async def purge_dismissed(db: AsyncSession, days: int) -> int:
    """
    物理删除"被用户删掉、且已经放了很久"的记忆行。

    软删除行的唯一用途是"告诉抽取任务这条别再写回来"。
    超过 days 天之后，这个作用也没意义了（用户大概率早忘了），
    留着只会让表慢慢变大。所以这里真的 DELETE。

    真正的 DELETE 只在清理任务里出现——它不涉及用户交互，
    不存在"删完又被写回来"的问题。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        delete(Memory).where(
            Memory.dismissed_at.is_not(None),
            Memory.dismissed_at < cutoff,
        )
    )
    await db.commit()
    return result.rowcount or 0
