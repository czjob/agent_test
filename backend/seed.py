"""
种子数据：启动时写入固定账号。

为什么不做注册接口：
  这是个人项目 / 演示场景，开放注册会引入验证码、邮箱验证、防刷等一整条链路。
  但 users 表、密码哈希、JWT 这套认证骨架是真的——将来要开放注册，
  只需要加一个 POST /auth/register，复用 hash_password 即可，其它代码一行不用改。

幂等：已存在的用户名直接跳过，重复启动不会报错也不会重复插入。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import User
from security import hash_password


async def seed_users(db: AsyncSession) -> list[str]:
    """确保 SEED_USERS 里的账号都存在，返回本次新建的用户名列表。"""
    created: list[str] = []

    for username, password, display_name in settings.seed_user_list():
        exists = await db.scalar(select(User.id).where(User.username == username))
        if exists is not None:
            continue

        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
            )
        )
        created.append(username)

    if created:
        await db.commit()

    return created
