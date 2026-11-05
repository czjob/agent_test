"""
数据库连接层：引擎、会话工厂、Base 基类。

关键设计：为什么流式接口不用 get_db 依赖
  FastAPI 的 Depends(get_db) 会在请求结束时释放 session。对于 SSE 长连接，
  "请求结束"发生在整条流推完之后，这意味着一个数据库连接会被一条几十秒的流占着。
  并发量低时看不出问题，但这是典型的连接泄漏姿势。
  所以 chat 路由在流式开始前用短 session 取完历史就立刻释放，
  流结束后再开一个新 session 落库——见 routers/chat.py。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

# 全局唯一引擎。pool_pre_ping：每次取连接前先 ping 一下，
# 避免数据库容器重启后拿到已失效的死连接（本地开发很容易遇到）。
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    echo=settings.DB_ECHO,
)

# expire_on_commit=False：commit 之后对象属性仍然可读。
# 默认 True 会在 commit 后把对象标记为过期，下次访问属性会触发一次新的查询，
# 而那时候 session 可能已经关闭了（异步下这会直接抛异常）。
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。SQLAlchemy 2.0 风格。"""


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个 session，请求结束自动关闭。"""
    async with SessionLocal() as session:
        yield session


async def init_schema() -> None:
    """
    建扩展 + 建表 + 补列。应用启动时调用一次。

    1. CREATE EXTENSION IF NOT EXISTS vector
       P0 还没有用到向量列，但先把扩展装上，P2 加 RAG 时不用再动数据库。
       用 try/except 包住：如果部署环境的账号不是超级用户，建扩展会失败，
       这时候不应该阻止整个服务启动。
    2. Base.metadata.create_all
       幂等：已存在的表会跳过。表结构稳定后再引入 Alembic 做版本化迁移。
    3. 轻量补列
       create_all 只建"不存在的表"，**不会给已存在的表加列**。
       P1 给 conversations 加了 summary_until_seq，老库里的表没有这一列，
       所以这里用 ALTER TABLE ... ADD COLUMN IF NOT EXISTS 补上。
    """
    from sqlalchemy import text

    # 必须先 import models，否则 Base.metadata 里是空的，create_all 什么都不会建
    import models  # noqa: F401

    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as exc:  # pragma: no cover - 依赖部署环境权限
            print(f"[db] 向量扩展创建失败（不影响当前功能）：{exc}")

        await conn.run_sync(Base.metadata.create_all)
        await _apply_light_migrations(conn)


# 老库升级用的幂等 DDL。
# 正式项目应该用 Alembic；这里是单机开发期，用 IF NOT EXISTS 兜住即可，
# 每加一条就往下追加一行，不回头改历史。
_LIGHT_MIGRATIONS = (
    # P1：摘要覆盖到哪条 seq。老库补列时给默认值 0（表示还没有摘要）
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary_until_seq INTEGER NOT NULL DEFAULT 0",
    # P1：记忆改为软删除（用户在面板里删掉的记忆不能被下一轮重新抽回来）
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ",
)


async def _apply_light_migrations(conn) -> None:
    """逐条执行幂等 DDL。失败只告警不中断——服务能起来比结构完美更重要。"""
    from sqlalchemy import text

    for ddl in _LIGHT_MIGRATIONS:
        try:
            await conn.execute(text(ddl))
        except Exception as exc:  # pragma: no cover
            print(f"[db] 迁移语句执行失败（{ddl[:60]}...）：{exc}")

