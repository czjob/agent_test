"""
后台任务的托管。

为什么需要这个文件（这是个真坑）：
  asyncio.create_task(coro) 返回的 Task 对象，如果没有任何强引用，
  事件循环只持弱引用，**垃圾回收随时可能把它回收掉**，任务就静默消失了。
  这不是理论问题，Python 官方文档专门写了警告。
  所以我在模块级用一个 set 持住所有在跑的任务，完成时自动移除。

第二个作用：应用关闭时能等它们跑完。
  lifespan 里收到退出信号时，如果直接把事件循环关掉，
  正在写数据库的后台任务会被硬中断，可能留下半截写入。
  这里提供 drain()，带超时地等一会儿。

为什么是进程内后台任务而不是 Celery：
  并发量低、单进程部署，一个协程池足够，少一个要运维的组件。
  将来上多 worker / 多机时，这里的接口形状不用变，把 spawn 的实现换成投递队列即可。
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# 持有所有在跑的后台任务，防止被 GC 回收
_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str = "") -> asyncio.Task | None:
    """
    把一个协程丢到后台执行，立即返回。

    内部已经包了异常兜底：后台任务抛出异常时没人 await 它，
    默认只会在解释器退出时打一句 "Task exception was never retrieved"。
    这里落一条日志，出问题能查到。
    """
    try:
        task = asyncio.create_task(coro, name=name or None)
    except RuntimeError:
        # 事件循环已关闭（比如应用正在退出）。此时直接丢弃任务，
        # 但要把协程关掉，否则会有 "coroutine was never awaited" 的警告。
        coro.close()
        logger.warning("事件循环不可用，后台任务已丢弃：%s", name or coro.__qualname__)
        return None

    _tasks.add(task)

    def _on_done(finished: asyncio.Task) -> None:
        _tasks.discard(finished)
        # cancelled() 为真时 exception() 会抛 CancelledError，必须先判断
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error("后台任务异常：%s", name or finished.get_name(), exc_info=exc)

    task.add_done_callback(_on_done)
    return task


async def drain(timeout: float = 10.0) -> None:
    """
    应用关闭时等待后台任务收尾。

    超时后强制取消：宁可丢几条记忆抽取，也不要让服务卡住关不掉。
    """
    if not _tasks:
        return

    pending = set(_tasks)
    logger.info("等待 %s 个后台任务收尾（最多 %.0f 秒）", len(pending), timeout)

    done, still_pending = await asyncio.wait(pending, timeout=timeout)

    for task in still_pending:
        task.cancel()

    if still_pending:
        logger.warning("%s 个后台任务超时未完成，已取消", len(still_pending))
