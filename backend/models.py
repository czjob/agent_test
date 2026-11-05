"""
ORM 模型：users / conversations / messages / memories

设计说明：
1. 不定义 ORM 关系（relationship）
   异步 SQLAlchemy 下，访问未预加载的关系属性会抛 MissingGreenlet（因为隐式 IO 在
   异步上下文里跑不了）。为了不踩这个坑，所有关联数据都用显式查询（见 services/），
   语义清楚，也不会出现"N+1 查询"的意外。

2. 时间统一用带时区的 DateTime（timestamptz）
   会话列表要按"今天 / 昨天"分组，如果存的是不带时区的时间，服务器时区和用户时区
   不一致时分组就会错。带时区存，前端拿到 ISO 字符串按本地时区渲染，最不容易出错。

3. 软删除用两个字段表达两个阶段
   archived_at：超过 30 天没更新，自动归档（列表不再返回，数据保留可恢复）
   deleted_at ：用户主动删除（同样只是标记，P1 再考虑是否需要彻底清除）
   两者分开，是为了让"系统归档"和"用户删除"在统计和恢复时能区分。

4. 摘要和消息原文并存
   summary 是 L2 压缩的产物，summary_until_seq 记录"摘要覆盖到哪条消息"。
   原文永远不删——用户要往前翻看完整历史；摘要只影响喂给模型的那份上下文。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db import Base

# 新会话的默认标题。前端判断"是否需要自动生成标题"时也用这个值。
DEFAULT_CONVERSATION_TITLE = "新会话"

# 长期记忆的三类。profile=身份信息，preference=稳定偏好，fact=长期有效的事实。
# 存字符串而不是 PG 枚举类型：加一类不需要 ALTER TYPE，改代码就能生效。
MEMORY_KINDS = ("profile", "preference", "fact")


class User(Base):
    """用户。前期不开放注册，由 seed.py 在启动时写入固定账号。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Conversation(Base):
    """会话。一条对话线程，归属于某个用户。"""

    __tablename__ = "conversations"
    __table_args__ = (
        # 侧边栏的查询是"某用户 + 按更新时间倒序 + 限 30 天"，
        # 这个联合索引正好覆盖前两个条件（倒序扫描正向索引即可）
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        # ondelete CASCADE：删用户时连带删会话（目前不会删用户，属于兜底）
        # index=True：所有查询都以 user_id 开头，必须有索引
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(120), default=DEFAULT_CONVERSATION_TITLE, nullable=False
    )
    # 对话摘要：L2 上下文压缩的产物，用于替代早期原始消息
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 摘要覆盖到哪条消息的 seq（含）。0 表示还没有任何摘要。
    # 这个字段是 L2 的关键：没有它，压缩之后就无法区分"哪些原文已经被摘要吸收"，
    # 每次都得重新压缩整段历史。
    summary_until_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 消息条数冗余字段：侧边栏要用，避免每次都 count(*) 全表扫
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # onupdate：任何一次 UPDATE 都会刷新它，侧边栏排序直接依赖这个字段
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # 系统自动归档时间（超过 ARCHIVE_AFTER_DAYS 天无更新）
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 用户主动删除时间
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base):
    """消息。用户说的、模型回的、工具返回的，都按顺序存在这张表里。"""

    __tablename__ = "messages"
    __table_args__ = (
        # seq 是会话内自增序号。唯一约束保证并发下不会写出两条相同序号的消息，
        # 同时它也是"按顺序取历史"的索引。
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # user / assistant / tool 三种。没有 system：系统提示每轮动态拼，不落库。
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # 仅 assistant 消息使用：模型这一轮请求调用了哪些工具（原始 tool_calls 结构）
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 仅 tool 消息使用：哪个工具、对应哪次调用
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Memory(Base):
    """
    长期记忆：从对话里抽取出来的、关于用户本人的稳定信息。

    和 messages 的区别：
      messages 记的是"说过什么"，按会话隔离，换个会话就看不到了；
      memories 记的是"这个人是什么样"，跨会话生效，注入到每轮的系统提示里。

    唯一约束 (user_id, content) 的用途：
      记忆抽取每轮都跑，很容易反复抽到同一句"用户是前端工程师"。
      靠数据库唯一约束 + ON CONFLICT 去重，比在应用层查一遍再判断更可靠
      （并发下也不会写出重复行）。
    """

    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("user_id", "content", name="uq_memories_user_content"),
        # 注入 prompt 时的查询是"某用户 + 按重要度倒序 + 阈值过滤"
        Index("ix_memories_user_importance", "user_id", "importance"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # profile / preference / fact，取值见 MEMORY_KINDS
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 0~1。抽取时由模型给出，用于排序和阈值过滤；用户手动确认的记忆可以拉满
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)

    # 来源会话。用 SET NULL 而不是 CASCADE：
    # 会话被清理掉时，记忆本身依然有效，不该跟着消失。
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 最近一次被注入 prompt 的时间，用于将来做"久未使用的记忆衰减"和面板排序
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 用户手动删除的时间。
    #
    # 为什么是软删除而不是 DELETE：
    #   记忆抽取每轮都在跑。物理删掉之后，下一轮对话只要又提到同样的内容，
    #   模型就会把它当成"新信息"重新写回来——用户会觉得"我删了它怎么又出现了"。
    #   留一行 dismissed_at，抽取时把它放进"不要重复提取"的名单，才能真正删掉。
    #
    #   这行数据本身没有保留价值，所以清理任务会定期物理删除 30 天前的记录。
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
