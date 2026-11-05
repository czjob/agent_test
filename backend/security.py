"""
认证层：密码哈希 + JWT 签发校验 + 当前用户依赖。

为什么不自己拼 hashlib：
  密码不能"可逆加密"，只能单向哈希。而普通 SHA256 的问题是算得太快，
  一张显卡每秒能试几十亿次。argon2 是专门为抗暴力破解设计的算法（内存硬），
  并且自带随机盐，同一个密码每次哈希结果都不同。
  pwdlib 是 passlib 的现代替代（passlib 已停止维护，且和 bcrypt 4.x 有兼容 bug）。
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import User

# PasswordHash.recommended() 会选用当前版本推荐的算法（argon2id）+ 推荐参数
_password_hash = PasswordHash.recommended()

# auto_error=False：没带 token 时不要直接抛 403，而是让下面的函数返回 401，
# 前端才能统一按"未登录"处理（跳登录页），而不是显示"禁止访问"。
_bearer = HTTPBearer(auto_error=False)


def hash_password(raw: str) -> str:
    return _password_hash.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    """校验密码。哈希格式损坏时返回 False 而不是抛异常，避免脏数据导致登录接口 500。"""
    try:
        return _password_hash.verify(raw, hashed)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    """签发 JWT。sub 放用户 id，另存 username 便于日志排查（不存密码等敏感信息）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解析并校验 JWT。签名不对 / 过期 / 格式错误都会抛异常，由调用方转成 401。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """
    所有需要登录的接口都挂这个依赖。

    为什么这里自己开 session，而不用 Depends(get_db)：
      流式接口（/chat/stream）是长时间存活的连接，而 FastAPI 的 Depends(get_db)
      要等到响应彻底结束才释放。鉴权如果复用那个 session，就会导致一条几十秒的流
      全程占着一个数据库连接。这里用独立的短 session 查完就关，
      返回的 User 对象脱离 session 后属性依然可读（已加载的值不会丢）。

    注意：这里每次请求都会查一次数据库确认用户仍然存在。
    并发量低的场景下这一次查询完全可以接受，换来的是"用户被删后旧 token 立即失效"，
    比纯靠 JWT 自证要安全。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证")

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.id == user_id))

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user
