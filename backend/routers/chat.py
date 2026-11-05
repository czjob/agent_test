"""
流式对话接口（SSE）。

这个文件是"持久化"、"记忆"和"Agent"三件事的交汇点，核心职责：
  1. 校验会话归属，把用户消息先落库（即使用户中途断开也不丢）
  2. 从数据库读上下文（L2 摘要 + 未压缩区的原文）和长期记忆（L3）
  3. 调用 agent.stream_agent 拿到事件流，边收边推 SSE
  4. 流结束后开一个**新的** session 把本轮产生的消息落库
  5. 流结束后触发两个后台任务：L2 摘要压缩、L3 记忆抽取

关于数据库连接：整条流式过程中不持有任何数据库连接。
准备阶段用完立刻释放，落库阶段重新取连接，避免长连接占满连接池。

关于两个后台任务的时机：
  它们都要调模型（1~3 秒），如果放在流式之前，用户要多等这么久才看到第一个字；
  放在流式之后，这一轮已经完整交付，用户关掉页面也不影响。
  所以统一走 background.spawn，不阻塞响应。
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from agent import build_system_prompt, stream_agent
from background import spawn
from config import settings
from db import SessionLocal
from models import DEFAULT_CONVERSATION_TITLE, Conversation
from schemas import ChatRequest
from security import get_current_user
from services import conversation as conv_service
from services import memory as memory_service
from services import summary as summary_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["对话"])

# SSE 报文格式：data: {...}\n\n（两个换行才算一条消息结束）
# ensure_ascii=False 让中文直接输出，报文更小也更好调试
_SSE_HEADERS = {
    "Cache-Control": "no-cache",      # 禁止中间层缓存，保证实时
    "Connection": "keep-alive",        # 保持长连接
    "X-Accel-Buffering": "no",         # 让 Nginx 不要缓冲（以后上反代时必需）
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _persist_produced(conversation_id: uuid.UUID, produced: list[BaseMessage]) -> None:
    """
    把本轮产生的 AI / 工具消息写入数据库。

    用独立 session，并且整体包 try/except：
    落库失败不该影响已经推送给用户的内容，只记日志。
    """
    if not produced:
        return

    try:
        async with SessionLocal() as db:
            conversation = await db.get(Conversation, conversation_id)
            if conversation is None:
                return

            for message in produced:
                if isinstance(message, ToolMessage):
                    await conv_service.append_message(
                        db,
                        conversation,
                        "tool",
                        str(message.content),
                        tool_name=message.name,
                        tool_call_id=message.tool_call_id,
                    )
                elif isinstance(message, AIMessage):
                    await conv_service.append_message(
                        db,
                        conversation,
                        "assistant",
                        str(message.content or ""),
                        tool_calls=message.tool_calls or None,
                    )
    except Exception:
        logger.exception("本轮消息落库失败，conversation_id=%s", conversation_id)


@router.post("/stream")
async def chat_stream(req: ChatRequest, current_user=Depends(get_current_user)) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        # ====================== 阶段一：准备（短 session，用完即释放） ======================
        async with SessionLocal() as db:
            conversation = await conv_service.get_conversation(
                db, current_user.id, req.conversation_id
            )
            if conversation is None:
                yield _sse({"type": "error", "content": "会话不存在或无权访问"})
                return

            conversation_id = conversation.id
            user_id = current_user.id

            # 用户消息先落库：即使用户中途关掉页面，这句话也不会丢
            user_message = await conv_service.append_message(db, conversation, "user", req.input)
            # 记录本轮起点，记忆抽取只处理这条之后的消息
            turn_seq = user_message.seq

            # 会话还是默认标题时，用首条消息生成标题，侧边栏才不会全是"新会话"
            if conversation.title == DEFAULT_CONVERSATION_TITLE:
                await conv_service.rename_conversation(
                    db, conversation, conv_service.make_title(req.input)
                )

            # L2：摘要 + 摘要线之后的原文
            summary, rows = await conv_service.load_context(db, conversation)
            history = conv_service.to_langchain_messages(rows)

            # L3：长期记忆（这一步会顺带刷新 last_used_at）
            memory_rows = await memory_service.load_for_prompt(db, user_id)

            total_messages = conversation.message_count
            need_compress = total_messages >= settings.SUMMARY_TRIGGER_MESSAGES
        # —— session 已释放，下面整条流不再占用数据库连接 ——

        # 记忆按效力分两组：
        #   偏好 → 对输出形式的硬性要求，必须遵守
        #   事实 → 只是背景，用来把问题讲对，不该影响回答长短
        # 具体原因见 agent.build_system_prompt 的注释（混在一起写偏好会失效）。
        preferences = [m.content for m in memory_rows if m.kind == "preference"]
        facts = [m.content for m in memory_rows if m.kind != "preference"]

        # 拼系统提示：基础人设 + 早期摘要 + 用户画像
        system_prompt = build_system_prompt(summary, preferences, facts)

        # L2 压缩放在后台跑：这一轮用旧摘要，下一轮就能用上新的，用户感知不到延迟。
        # 幂等：压缩任务自己会判断"够不够压"，重复触发不会写出错误数据。
        if need_compress:
            spawn(
                summary_service.compress_if_needed(conversation_id),
                name=f"compress:{conversation_id}",
            )

        # ====================== 阶段二：流式生成 ======================
        produced: list[BaseMessage] = []
        try:
            async for event in stream_agent(history, produced, system_prompt=system_prompt):
                yield _sse({"type": event.type, "content": event.content})
        except asyncio.CancelledError:
            # 用户中途断开（关页面 / 点新会话）。已生成的部分照样落库，然后正常退出。
            raise
        finally:
            await _persist_produced(conversation_id, produced)

            # L3 抽取放在落库之后：它要读数据库里的这一轮对话，落库没完成就抽不到。
            # 只有真正产出了内容才抽——模型直接报错的话没有抽取价值。
            if produced:
                spawn(
                    memory_service.extract_from_turn(conversation_id, user_id, turn_seq),
                    name=f"memory:{conversation_id}",
                )

        # ====================== 阶段三：结束标记 ======================
        # 前端收到 done 才解除 loading 状态
        yield _sse({"type": "done"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
