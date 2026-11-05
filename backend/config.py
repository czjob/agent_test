"""
配置统一入口。

为什么单独一个文件：
  环境变量的读取散落在各处会变成灾难——改一个默认值要全局搜。这里集中读取一次，
  其他模块只 import settings，不直接碰 os.getenv。
  另外 load_dotenv 只在这里调用一次，避免"某个模块先被导入导致 .env 还没加载"的时序坑。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录的绝对路径。用 __file__ 推导而不是写死，项目挪位置也不会崩。
BASE_DIR = Path(__file__).resolve().parent

# 读取 backend/.env 注入环境变量（该文件已被 .gitignore 排除）
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    """读整数型环境变量。值不合法时回退默认值，而不是让服务起不来。"""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    """读浮点型环境变量，同样做兜底。"""
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    """读布尔型环境变量。接受 true/1/yes/on 四种写法。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


class Settings:
    """全部配置项。命名与 .env 里的变量名一一对应，便于对照排查。"""

    # ---------- 数据库 ----------
    # 注意主机用 127.0.0.1 而不是 localhost：
    # Windows 上 localhost 会优先解析到 IPv6 ::1，而 Docker 端口映射虽然监听了 [::]，
    # 但部分环境下仍会握手失败。写死 IPv4 最稳。
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/agent_demo",
    )
    # 连接池：并发量低，5 个常驻足够；max_overflow 兜住突发
    DB_POOL_SIZE: int = _int("DB_POOL_SIZE", 5)
    DB_MAX_OVERFLOW: int = _int("DB_MAX_OVERFLOW", 10)
    DB_ECHO: bool = os.getenv("DB_ECHO", "false").lower() == "true"

    # ---------- 认证 ----------
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-only-secret-change-me")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = _int("JWT_EXPIRE_MINUTES", 60 * 24 * 7)  # 7 天

    # ---------- 大模型 ----------
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")

    # ---------- 对话行为 ----------
    # 每次请求最多带多少条历史消息给模型（防止上下文无限膨胀）
    HISTORY_WINDOW: int = _int("HISTORY_WINDOW", 40)
    # Agent 循环最大轮数（模型调工具 -> 看结果 -> 再调工具，防死循环）
    MAX_AGENT_ROUNDS: int = _int("MAX_AGENT_ROUNDS", 8)
    # token 推送最小间隔（秒），营造打字机节奏；设 0 表示完全跟随模型速度
    TOKEN_MIN_INTERVAL: float = float(os.getenv("TOKEN_MIN_INTERVAL", "0.03"))

    # ---------- L2 上下文压缩 ----------
    # 会话消息总数超过这个值，才进入"可以考虑压缩"的候选
    SUMMARY_TRIGGER_MESSAGES: int = _int("SUMMARY_TRIGGER_MESSAGES", 24)
    # 压缩时始终保留最近多少条原文不压（保证模型能看到最新上下文）
    SUMMARY_KEEP_RECENT: int = _int("SUMMARY_KEEP_RECENT", 10)
    # 一次压缩至少要覆盖多少条消息才值得调模型（太少就是浪费 token）
    SUMMARY_MIN_BATCH: int = _int("SUMMARY_MIN_BATCH", 8)
    # 摘要字数上限，同时也是注入 prompt 时的硬约束
    SUMMARY_MAX_CHARS: int = _int("SUMMARY_MAX_CHARS", 500)
    # 单条消息在压缩输入里的截断长度，防止一条超长回答把 prompt 撑爆
    SUMMARY_SOURCE_CLIP: int = _int("SUMMARY_SOURCE_CLIP", 400)

    # ---------- L3 长期记忆 ----------
    MEMORY_ENABLED: bool = _bool("MEMORY_ENABLED", True)
    # 注入 prompt 的记忆条数上限
    MEMORY_TOP_K: int = _int("MEMORY_TOP_K", 20)
    # 低于该重要度的记忆不注入（避免一堆噪音影响回答）
    MEMORY_MIN_IMPORTANCE: float = _float("MEMORY_MIN_IMPORTANCE", 0.3)
    # 记忆正文长度上限，写入前会截断
    MEMORY_MAX_CHARS: int = _int("MEMORY_MAX_CHARS", 200)
    # 抽取时把这轮对话提交给模型的字符预算
    MEMORY_SOURCE_CLIP: int = _int("MEMORY_SOURCE_CLIP", 3000)

    # ---------- 会话生命周期 ----------
    # 列表只展示最近 N 天有更新的会话
    CONVERSATION_LIST_DAYS: int = _int("CONVERSATION_LIST_DAYS", 30)
    # 超过 N 天没更新的会话自动归档（列表查不到，数据保留）
    ARCHIVE_AFTER_DAYS: int = _int("ARCHIVE_AFTER_DAYS", 30)
    # 归档超过 N 天的会话物理删除
    PURGE_AFTER_DAYS: int = _int("PURGE_AFTER_DAYS", 30)
    # 清理任务执行间隔（秒），默认 6 小时
    CLEANUP_INTERVAL_SECONDS: int = _int("CLEANUP_INTERVAL_SECONDS", 6 * 3600)

    # ---------- 种子用户 ----------
    # 格式：用户名:密码:显示名，逗号分隔
    SEED_USERS: str = os.getenv(
        "SEED_USERS",
        "alice:demo1234:爱丽丝,bob:demo1234:鲍勃,carol:demo1234:卡罗尔",
    )

    def seed_user_list(self) -> list[tuple[str, str, str]]:
        """把 SEED_USERS 解析成 [(username, password, display_name), ...]。"""
        result: list[tuple[str, str, str]] = []
        for chunk in self.SEED_USERS.split(","):
            parts = chunk.strip().split(":")
            if len(parts) != 3:
                continue
            result.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
        return result


settings = Settings()
