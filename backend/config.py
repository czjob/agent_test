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

    # ---------- P2：向量化（embedding） ----------
    # 当前接的是硅基流动的 bge-m3：1024 维，中文效果够用。
    #
    # 换服务商只需要改这四个值，代码不用动：
    #   智谱 BigModel   BASE_URL=https://open.bigmodel.cn/api/paas/v4
    #                   MODEL=embedding-3  DIM=1024  DIMENSIONS=1024
    #                   （模型默认输出 2048 维，超过 pgvector 的 HNSW 上限 2000，
    #                     必须用 dimensions 参数压到 1024，否则索引建不起来）
    #   阿里百炼         BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    #                   MODEL=text-embedding-v3  DIM=1024
    EMBED_API_KEY: str = os.getenv("EMBED_API_KEY", "")
    EMBED_BASE_URL: str = os.getenv("EMBED_BASE_URL", "https://api.siliconflow.cn/v1")
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
    EMBED_DIM: int = _int("EMBED_DIM", 1024)
    # 请求时显式指定输出维度，0 表示不传这个字段。
    #
    # 为什么需要它：部分模型支持"按需输出维度"（OpenAI text-embedding-3、
    # 智谱 embedding-3 都是），默认维度往往很大（2048 / 3072），
    # 而 pgvector 的 HNSW 索引只到 2000 维。压到 1024 后既能用索引，
    # 存储和传输也省一半，检索质量在中文场景几乎无差异。
    # 不支持的模型（比如 bge-m3）保持 0，避免多传字段被拒。
    EMBED_DIMENSIONS: int = _int("EMBED_DIMENSIONS", 0)
    # 单次请求带多少段文本。硅基流动 bge-m3 的单批上限是 32，这里留余量
    EMBED_BATCH_SIZE: int = _int("EMBED_BATCH_SIZE", 16)
    EMBED_TIMEOUT: float = _float("EMBED_TIMEOUT", 30.0)

    # ---------- P2：知识库（RAG） ----------
    RAG_ENABLED: bool = _bool("RAG_ENABLED", True)
    # 上传文件大小上限。超过直接拒，不进入解析流程
    UPLOAD_MAX_BYTES: int = _int("UPLOAD_MAX_BYTES", 5 * 1024 * 1024)
    # 抽取出的正文上限。防止一个巨型 PDF 把后台向量化任务拖到几分钟
    DOCUMENT_MAX_CHARS: int = _int("DOCUMENT_MAX_CHARS", 200_000)
    # 切片长度与重叠。500 字大约对应 300~500 token，是检索粒度与上下文完整性的折中
    CHUNK_SIZE: int = _int("CHUNK_SIZE", 500)
    CHUNK_OVERLAP: int = _int("CHUNK_OVERLAP", 80)
    # 一次检索返回几段
    RAG_TOP_K: int = _int("RAG_TOP_K", 5)
    # 相似度低于这个值的结果丢掉。
    #
    # 0.45 是拿 bge-m3 实测出来的，两组数据：
    #
    # 1) 句子级对照（12 组）
    #    无关对 "红烧肉要炖多久" vs "Vite 预构建机制"：0.237 ~ 0.414，均值 0.337
    #    相关对 "构建优化怎么做的" vs "预构建缓存持久化…"：0.538 ~ 0.812，均值 0.639
    #    初版配的 0.35 压在无关区间 80 分位上，"唐诗三百首"这类内容会被当成资料塞进上下文。
    #
    # 2) 真实文档检索（一份 4 章节笔记，17 个查询，Top-1 命中 10/10）
    #    正确命中的最低分是 0.4900（"shadow DOM 用来干什么"、"炒糖色用什么火"），
    #    但同一文档里邻近章节作噪声时最高能到 0.5789。
    #    两者区间重叠，说明**阈值挡不住"同文档里相邻但不对题"的片段**。
    #
    # 所以要认清这个参数的职责：它只负责挡掉"完全离题"的问题
    # （"今天天气怎么样"、"公司年假有几天"，实测返回空），
    # 文档内部的排序交给 top-k 和模型自己判断，别指望阈值解决。
    # 也因此**不能**调到 0.5：那条线会先筛掉两条正确答案，噪声照样进得来。
    #
    # 这个值**与模型强相关**，不能跨模型照抄：
    #   bge-m3 的分布整体偏高且压缩（无关也有 0.33），
    #   有些模型无关文本只有 0.05，用 0.45 会把正确结果全筛掉。
    #   换 embedding 模型必须重新实测，方法见 README「换向量模型」。
    RAG_MIN_SCORE: float = _float("RAG_MIN_SCORE", 0.45)
    # 工具返回给模型的检索结果总字符预算，防止一次塞爆上下文
    RAG_TOOL_MAX_CHARS: int = _int("RAG_TOOL_MAX_CHARS", 3000)

    # ---------- P2：记忆向量召回 ----------
    # 记忆条数超过 MEMORY_TOP_K 之后，事实类改为按语义相似度召回。
    # 偏好类永远全量注入，理由见 services/memory.load_for_prompt
    MEMORY_VECTOR_RECALL: bool = _bool("MEMORY_VECTOR_RECALL", True)

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
