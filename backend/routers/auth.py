"""认证接口：登录、获取当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import get_db
from models import User
from schemas import LoginRequest, LoginResponse, UserOut
from security import create_access_token, get_current_user, verify_password

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    """
    登录换 token。

    安全细节：用户名不存在和密码错误返回**完全相同**的提示。
    如果分开提示，攻击者就能靠"用户不存在"这个响应枚举出哪些账号是有效的。
    """
    user = await db.scalar(select(User).where(User.username == payload.username))

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    token = create_access_token(user)
    return LoginResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    """
    前端刷新页面时用它验证本地 token 是否还有效。
    有效就直接进主界面，无效（401）就回登录页——避免"页面闪一下再被踢出去"。
    """
    return UserOut.model_validate(current_user)
