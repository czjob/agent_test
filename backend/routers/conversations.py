"""会话管理接口：列表、新建、详情、重命名、删除、清空消息。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Conversation, User
from schemas import (
    ConversationDetail,
    ConversationOut,
    CreateConversationRequest,
    MessageOut,
    RenameConversationRequest,
)
from security import get_current_user
from services import conversation as conv_service

router = APIRouter(prefix="/conversations", tags=["会话"])


async def _require_owned(
    db: AsyncSession, user: User, conversation_id: uuid.UUID
) -> Conversation:
    """
    取出会话并校验归属。

    不属于当前用户时返回 404 而不是 403：
    403 等于告诉对方"这个 id 存在，只是你没权限"，可以被用来探测别人的会话是否存在。
    """
    conversation = await conv_service.get_conversation(db, user.id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    """侧边栏列表：只返回最近 30 天有更新的、未删除、未归档的会话。"""
    rows = await conv_service.list_conversations(db, current_user.id)
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: CreateConversationRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """
    新建会话。不传 title 就用默认标题"新会话"，
    等用户发第一条消息时由后端自动改成消息摘要（见 routers/chat.py）。
    """
    conversation = await conv_service.create_conversation(
        db, current_user.id, payload.title if payload else None
    )
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation_detail(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    """进入某个会话时拉取全部消息，用于渲染历史。"""
    conversation = await _require_owned(db, current_user, conversation_id)
    messages = await conv_service.load_messages(db, conversation.id)

    detail = ConversationDetail.model_validate(conversation)
    detail.messages = [MessageOut.model_validate(message) for message in messages]
    return detail


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID,
    payload: RenameConversationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await _require_owned(db, current_user, conversation_id)
    updated = await conv_service.rename_conversation(db, conversation, payload.title)
    return ConversationOut.model_validate(updated)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """软删除：只标记 deleted_at，数据留在库里，误删可恢复。"""
    conversation = await _require_owned(db, current_user, conversation_id)
    await conv_service.soft_delete_conversation(db, conversation)


@router.delete("/{conversation_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_conversation_messages(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """清空消息但保留会话本身（相当于"重新开始这段对话"）。"""
    conversation = await _require_owned(db, current_user, conversation_id)
    await conv_service.clear_messages(db, conversation)
