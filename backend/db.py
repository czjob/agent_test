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
        # 维度重建是"整体原子"的：要么列改完+索引建好，要么完全不动。
        # 失败不能拦住启动，所以包一层——但必须打印出来，
        # 因为维度不一致时静默失败的后果是"上传永远处理不完"。
        try:
            async with conn.begin_nested():
                await _ensure_embedding_dim(conn)
        except Exception as exc:  # pragma: no cover
            print(f"[db] 向量列重建失败，检索可能不可用：{exc}")

        # 索引必须等维度确定之后再建，否则会拿新维度去评估旧列
        try:
            async with conn.begin_nested():
                await _ensure_chunk_index(conn)
        except Exception as exc:  # pragma: no cover
            print(f"[db] 向量索引创建失败，检索仍可用但会变慢：{exc}")


# pgvector 的 HNSW 索引对维度有硬上限：vector 类型最多 2000 维。
# 超过会直接报 "column cannot have more than 2000 dimensions for hnsw index"，
# 而且是 DDL 级别的失败，会把整个建表事务一起回滚。
#
# 现实影响：主流中文向量模型里，bge-m3 是 1024 维（能建），
# 智谱 embedding-3 默认 2048 维（建不了），OpenAI text-embedding-3-large 是 3072 维（建不了）。
# 遇到这种情况有三条路，按推荐顺序：
#   1. 让接口少输出点维度（多数模型支持 dimensions 参数，智谱可以传 1024）
#   2. 列类型改 halfvec(N)，半精度存储，HNSW 支持到 4000 维，召回几乎无损
#   3. 放弃索引走顺序扫描——几万条以内完全够用，只是文档多了会慢
_HNSW_MAX_DIM = 2000


# 老库升级用的幂等 DDL。
# 正式项目应该用 Alembic；这里是单机开发期，用 IF NOT EXISTS 兜住即可，
# 每加一条就往下追加一行，不回头改历史。
_LIGHT_MIGRATIONS = (
    # P1：摘要覆盖到哪条 seq。老库补列时给默认值 0（表示还没有摘要）
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary_until_seq INTEGER NOT NULL DEFAULT 0",
    # P1：记忆改为软删除（用户在面板里删掉的记忆不能被下一轮重新抽回来）
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ",
    # P2：记忆的语义向量列。
    # 注意 vector(N) 的维度在这里是写死的：改了 EMBED_DIM 之后这条语句不会生效
    # （列已存在），实际改维度由 _ensure_embedding_dim 负责。
    f"ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding vector({settings.EMBED_DIM})",
    # P2：切片的向量索引**不在这里建**。
    # 它依赖当前列的真实维度，而列可能是老维度（等待 _ensure_embedding_dim 修改），
    # 在这个位置建会必然失败一次，白刷一条报错。
    # 统一交给 _ensure_chunk_index，在维度确定之后再决定建不建。
    # P2：清掉建表时自动生成的两个冗余单列索引。
    # 起因是 P2 第一次建表时给 user_id 同时写了 index=True 和显式联合索引，
    # 结果同一列上出现两个功能重叠的 btree。留着不影响正确性，
    # 但每次写入都要多维护一棵树。改成只保留显式索引，这里负责把旧的清掉。
    "DROP INDEX IF EXISTS ix_chunks_user_id",
    "DROP INDEX IF EXISTS ix_documents_user_id",
)


async def _apply_light_migrations(conn) -> None:
    """
    逐条执行幂等 DDL。失败只告警不中断——服务能起来比结构完美更重要。

    每条 DDL 用 SAVEPOINT 包起来的原因（踩过才知道）：
      这些语句跑在同一个事务里。PostgreSQL 的规则是——事务内任意一条语句报错，
      整个事务就进入 aborted 状态，后续所有语句都返回
      "current transaction is aborted, commands ignored until end of transaction block"。
      也就是说外层这个 try/except 只能接住第一条失败，
      后面每一条都会因为同一个原因失败，看起来像"所有迁移都坏了"。
      加 SAVEPOINT 后，失败只回滚这一条，后面的语句在干净的事务上继续执行。
    """
    from sqlalchemy import text

    for ddl in _LIGHT_MIGRATIONS:
        try:
            async with conn.begin_nested():
                await conn.execute(text(ddl))
        except Exception as exc:  # pragma: no cover
            print(f"[db] 迁移语句执行失败（{ddl[:60]}...）：{exc}")


async def _read_embedding_dim(conn, table: str) -> int | None:
    """
    读某张表 embedding 列声明的维度。表或列不存在时返回 None。

    to_regclass 在表不存在时返回 NULL 而不是报错，所以这条查询是安全的。
    """
    from sqlalchemy import text

    result = await conn.execute(
        text(
            "SELECT a.atttypmod FROM pg_attribute a "
            "WHERE a.attrelid = to_regclass(:tbl) "
            "AND a.attname = 'embedding' AND a.atttypmod > 0"
        ),
        {"tbl": f"public.{table}"},
    )
    return result.scalar()


async def _ensure_embedding_dim(conn) -> None:
    """
    保证数据库里的向量维度与 EMBED_DIM 一致，不一致就自动重建。

    为什么从"检查并告警"改成"直接重建"：
      换 embedding 模型时最容易犯的错是只改 .env 不改表结构。
      原来的实现只打一行告警，结果服务能起来、上传也能提交，
      失败却发生在后台向量化任务里，错误信息是 "expected 1024 dimensions"，
      看起来像"上传坏了"。开发期没人会去翻启动日志找那一行告警。
      既然向量和模型天然绑定、换模型时旧向量本来就必须作废，
      不如在这里一次性做掉：清空向量 → 改列类型 → 重建索引 → 标记待重索引。

    为什么要先 DROP INDEX：
      pgvector 的 HNSW 索引绑定了维度，直接 ALTER COLUMN TYPE 会因为
      "索引定义依赖该列"而失败。先拆后建最省事。

    为什么要 SET NULL 而不是 CAST：
      1024 维和 2048 维之间没有有意义的换算关系，CAST 要么直接报错，
      要么（如果写 USING 表达式）产生一堆语义错误的垃圾向量。
      换模型就必须重新算向量，清空是唯一正确的选择。

    为什么要把 documents 打回 pending：
      chunks 的向量被清空后，文档的 status 仍是 ready，
      而检索 SQL 的条件是 status='ready' AND embedding IS NOT NULL——
      结果是"文档显示可检索，但怎么问都查不到"，属于最难排查的那类静默失败。
    """
    from sqlalchemy import text

    target = settings.EMBED_DIM
    db_dim = await _read_embedding_dim(conn, "chunks")
    if db_dim is None or db_dim == target:
        return

    print(f"[db] 向量维度变更 {db_dim} -> {target}，开始重建向量列（旧向量将作废）")

    # 索引跟着列维度走，必须先拆掉，改完维度后由 _ensure_chunk_index 重建
    await conn.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
    for table in ("chunks", "memories"):
        await conn.execute(
            text(
                f"ALTER TABLE {table} ALTER COLUMN embedding "
                f"TYPE vector({target}) USING NULL::vector({target})"
            )
        )
    # 记忆的向量由 _backfill_embeddings 在下一轮对话时自动补齐，不用额外处理
    await conn.execute(
        text(
            "UPDATE documents SET status = 'pending', "
            "error = '向量模型已切换，需要重新索引' WHERE status = 'ready'"
        )
    )
    print("[db] 向量列已重建。原有文档已标记为待重新索引，记忆向量会自动补算。")


async def _ensure_chunk_index(conn) -> None:
    """
    按当前维度决定建不建 HNSW 索引。

    为什么用 HNSW 而不是 IVFFlat：
      IVFFlat 需要先用已有数据"训练"出聚类中心，表是空的时候建索引没有意义，
      而且新写入的数据可能落在没被覆盖的区域导致查不到。
      HNSW 是图结构，插入即生效，适合"用户随时上传、随查随时有"的场景。
      代价是建索引更慢、占内存更多——这个规模完全扛得住。

    vector_cosine_ops 对应 <=> 操作符，检索时必须用同一个距离函数，
    否则索引用不上，会退化成全表扫描（小数据量看不出差异，数据一多就暴露）。
    """
    from sqlalchemy import text

    if settings.EMBED_DIM <= _HNSW_MAX_DIM:
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
                "ON chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
        return

    # 超限时硬建会让 DDL 失败，进而把整个事务拖成 aborted，连列都改不掉。
    # 退化成顺序扫描：正确性不受影响，只是切片多了以后变慢。
    # 几万条切片做暴力比对通常是几十毫秒，个人项目完全够用。
    await conn.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
    print(
        f"[db] EMBED_DIM={settings.EMBED_DIM} 超过 HNSW 上限 {_HNSW_MAX_DIM}，已跳过向量索引。"
        "检索会走全表扫描（结果正确，数据量大时变慢）。"
        "要恢复索引，请让模型输出 2000 维以内，或把列类型改为 halfvec。"
    )

