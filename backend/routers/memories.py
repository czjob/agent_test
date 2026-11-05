"""
长期记忆接口：给前端记忆面板用。

设计意图：记忆如果只是"后端偷偷记着"，用户就不信任它。
所以必须能看得见、能删掉——这三条接口就是"看得见"和"能删掉"。

权限规则与会话接口一致：所有查询和删除的 SQL 都带 user_id 条件，
不靠"先查出来再判断归属"，那样一旦某个接口漏判就是越权漏洞。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import User
from schemas import ClearMemoryResponse, MemoryOut
from security import get_current_user
from services import memory as memory_service

router = APIRouter(prefix="/memories", tags=["长期记忆"])


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemoryOut]:
    """当前用户的全部长期记忆，按重要度倒序。"""
    rows = await memory_service.list_memories(db, current_user.id)
    return [MemoryOut.model_validate(row) for row in rows]


@router.delete("", response_model=ClearMemoryResponse)
async def clear_memories(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClearMemoryResponse:
    """
    清空全部记忆（相当于"忘掉我"）。

    返回删除条数而不是 204：面板上要给用户一句"已清除 12 条"的反馈，
    否则点完什么都不发生，会让人怀疑没生效。
    """
    deleted = await memory_service.clear_memories(db, current_user.id)
    return ClearMemoryResponse(deleted=deleted)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    删除单条记忆。

    不存在和不属于当前用户都返回 404，理由同会话接口：
    403 会泄露"这条记忆存在，只是不是你的"。
    """
    deleted = await memory_service.delete_memory(db, current_user.id, memory_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在")
