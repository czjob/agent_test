"""
L2 上下文压缩：把早期消息压成一段摘要，替代原文喂给模型。

解决什么问题：
  对话到 40 条以后，光是把历史原文塞进 prompt 就要几千 token，
  而且 HISTORY_WINDOW 一截断，早期信息就永久丢失了——
  用户说"我最开始跟你说的那个需求"，模型完全不知道指的是什么。

本模块的做法：
  保留最近 SUMMARY_KEEP_RECENT 条原文（保证模型能接住当前话题），
  更早的全部压成一段 summary 存进 conversations.summary，
  并把"压到哪一条"记在 summary_until_seq 里。
  下一轮拼 prompt 时 = system(摘要 + 记忆) + 未压缩区的原文。

为什么不做成同步流程：
  压缩要调一次模型，1~3 秒。放在流式开始前会让用户多等这么久才看到第一个字。
  所以它跑在后台（background.spawn），这一轮用旧摘要，下一轮就能用上新的——
  用户完全感知不到延迟。

原文永远不删：
  摘要只是喂给模型的"压缩上下文"，前端要展示完整历史，
  所以 messages 表里一条都不动。
"""

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, select

from config import settings
from db import SessionLocal
from llm import llm
from models import Conversation, Message

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """你是对话摘要器。把「已有摘要」和「新增对话」合并成一段新的摘要。

已有摘要（更早的对话，可能为空）：
{old_summary}

新增对话：
{transcript}

输出要求：
- 一段连贯的中文，不要分点、不要标题、不要任何前缀说明
- 保留：用户明确说过的身份、偏好、目标；已经确认的事实和结论；任务的当前进展
- 丢弃：寒暄、重复内容、工具调用的中间过程（只保留最终结论）
- 用陈述句直接写事实，不要写"用户说""我回答了"这类引述
- 严格不超过 {max_chars} 字"""


def _render_transcript(rows: list[Message]) -> str:
    """
    把消息行渲染成紧凑的对话文本。

    工具消息只保留结论且截断得更狠：压缩的目的是留住"发生了什么"，
    不是留住计算器返回了 12 位小数。
    """
    lines: list[str] = []
    for row in rows:
        content = " ".join((row.content or "").split())
        if not content:
            continue

        if row.role == "tool":
            # 工具结果截得更短，只留结论
            clipped = content[:200] + ("…" if len(content) > 200 else "")
            lines.append(f"[工具 {row.tool_name or ''}] {clipped}")
        elif row.role == "user":
            clipped = content[: settings.SUMMARY_SOURCE_CLIP]
            lines.append(f"用户：{clipped}")
        else:
            clipped = content[: settings.SUMMARY_SOURCE_CLIP]
            lines.append(f"助手：{clipped}")

    return "\n".join(lines)


async def _summarize(old_summary: str | None, rows: list[Message]) -> str:
    """调一次模型生成新摘要。失败时抛异常，由调用方兜底。"""
    transcript = _render_transcript(rows)
    prompt = SUMMARY_PROMPT.format(
        old_summary=(old_summary or "（暂无）").strip(),
        transcript=transcript,
        max_chars=settings.SUMMARY_MAX_CHARS,
    )

    response = await llm.ainvoke(
        [SystemMessage(content="你擅长把长对话压缩成准确、无遗漏的摘要。"), HumanMessage(content=prompt)]
    )
    summary = str(response.content or "").strip()
    # 模型偶尔会超字数，硬截断一次，避免 summary 无限膨胀
    return summary[: settings.SUMMARY_MAX_CHARS * 2]


async def compress_if_needed(conversation_id: uuid.UUID) -> None:
    """
    后台任务入口：判断这个会话是否需要压缩，需要就压一次。

    自己开 session（后台任务没有请求上下文，拿不到 Depends 注入的 session），
    并在整个过程中不持有任何跨 await 之外的状态。
    """
    try:
        async with SessionLocal() as db:
            conversation = await db.get(Conversation, conversation_id)
            # 会话可能已经被删了（用户删得比后台任务跑得快），直接放弃
            if conversation is None or conversation.deleted_at is not None:
                return

            total = await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )
            total = total or 0

            # 还没到阈值，什么都不用做
            if total < settings.SUMMARY_TRIGGER_MESSAGES:
                return

            # 找出"保留区"的分界线：从末尾数第 keep_recent 条的 seq
            keep = max(settings.SUMMARY_KEEP_RECENT, 2)
            if total <= keep:
                return
            cutoff_seq = await db.scalar(
                select(Message.seq)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq.desc())
                .offset(keep - 1)
                .limit(1)
            )
            if cutoff_seq is None:
                return

            # 待压缩 = 上次摘要之后、保留区之前的部分
            already = conversation.summary_until_seq or 0
            if cutoff_seq <= already:
                # 保留区已经全部被之前某次摘要覆盖过了，没有新增内容可压
                return

            rows = list(
                (
                    await db.scalars(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation_id,
                            Message.seq > already,
                            Message.seq <= cutoff_seq,
                        )
                        .order_by(Message.seq.asc())
                    )
                ).all()
            )

            # 攒得太少不值得调模型：等下一轮消息多了再一起压
            if len(rows) < settings.SUMMARY_MIN_BATCH:
                return

            new_summary = await _summarize(conversation.summary, rows)
            if not new_summary:
                return

            updated_at = conversation.updated_at
            conversation.summary = new_summary
            conversation.summary_until_seq = cutoff_seq
            # 保留 updated_at 原值：这一列带 onupdate=func.now()，
            # 只要这行被 UPDATE 就会刷新，侧边栏排序会莫名其妙跳到最前面，
            # 看起来像用户刚发过消息。显式赋回原值，SQLAlchemy 就按显式值写入。
            conversation.updated_at = updated_at
            await db.commit()

            logger.info(
                "会话 %s 摘要压缩完成：%s 条 -> %s 字（覆盖到 seq=%s）",
                conversation_id,
                len(rows),
                len(new_summary),
                cutoff_seq,
            )
    except Exception:
        # 压缩失败不影响对话本身，下一轮再试即可
        logger.exception("摘要压缩失败，conversation_id=%s", conversation_id)
