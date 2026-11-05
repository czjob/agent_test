"""
FastAPI 应用装配入口。

这个文件只做三件事：注册中间件、挂路由、管生命周期。
业务逻辑都在 routers/ 和 services/ 里——想改功能不用看这里。

启动：
    python -m uvicorn main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from background import drain
from db import SessionLocal, engine, init_schema
from routers import auth, chat, conversations, memories
from seed import seed_users
from services import memory as memory_service
from services.conversation import run_retention_cleanup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def _retention_loop() -> None:
    """
    会话生命周期清理任务。

    为什么用 asyncio 循环而不是 APScheduler / Celery：
    并发量低、单进程部署，一个后台协程足够。少一个依赖，少一个要运维的组件。
    将来上多 worker 时，这个任务会被每个进程各跑一遍（幂等，不会出错，只是浪费），
    到时候再换成独立进程的定时任务即可。
    """
    while True:
        await asyncio.sleep(settings.CLEANUP_INTERVAL_SECONDS)
        try:
            async with SessionLocal() as db:
                archived, purged = await run_retention_cleanup(db)
                # 记忆的软删除行也要清：它只为"拦住重复抽取"存在，过期就没用了
                dismissed = await memory_service.purge_dismissed(db, settings.PURGE_AFTER_DAYS)
            if archived or purged or dismissed:
                logger.info(
                    "定期清理：归档会话 %s 个，删除会话 %s 个，清除失效记忆 %s 条",
                    archived,
                    purged,
                    dismissed,
                )
        except Exception:
            # 清理失败不能把服务搞挂，记日志等下一轮
            logger.exception("定期清理任务执行失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 / 关闭时执行：建表、写种子用户、拉起清理任务。"""
    await init_schema()

    async with SessionLocal() as db:
        created = await seed_users(db)
    if created:
        logger.info("已创建种子用户：%s", ", ".join(created))
    logger.info("数据库就绪：%s", settings.DATABASE_URL.rsplit("@", 1)[-1])

    cleanup_task = asyncio.create_task(_retention_loop())

    yield

    cleanup_task.cancel()

    # 等后台任务收尾：摘要压缩、记忆抽取可能正在写库，
    # 直接关事件循环会把它们硬中断。带超时，避免服务关不掉。
    await drain(timeout=10.0)

    await engine.dispose()


app = FastAPI(title="LangChain Agent Demo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(memories.router)


@app.get("/")
async def root() -> dict:
    """探活接口：curl http://localhost:8000/ 能通就说明服务起来了。"""
    return {"status": "ok", "message": "Agent 后端服务运行中"}
