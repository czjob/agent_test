"""
接口的请求 / 响应模型（Pydantic）。

和 ORM 模型分开的原因：
  ORM 模型描述"数据库怎么存"，接口模型描述"前端看到什么"。
  两者合并会把 password_hash 这类字段泄漏到响应里，也会让数据库结构调整直接破坏前端契约。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ====================== 用户 / 认证 ======================
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, description="用户名")
    password: str = Field(min_length=1, max_length=128, description="密码")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="有效期秒数，前端可据此做提前续期提示")
    user: UserOut


# ====================== 会话 ======================
class ConversationOut(BaseModel):
    """侧边栏列表项。不含消息正文，列表接口返回轻量数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    tool_calls: list | None = None
    tool_name: str | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    """进入某个会话时返回的完整数据。"""

    summary: str | None = None
    messages: list[MessageOut] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    """新建会话。title 不传则用默认标题，之后由首条消息自动生成。"""

    title: str | None = Field(default=None, max_length=120)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


# ====================== 对话 ======================
class ChatRequest(BaseModel):
    conversation_id: uuid.UUID
    input: str = Field(min_length=1, description="用户本轮输入")


# ====================== 长期记忆 ======================
class MemoryOut(BaseModel):
    """记忆面板的一条。不含 user_id / source_conversation_id——
    面板只需要展示"记住了什么"，多给 ID 只会让人以为可以拿来调接口。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str = Field(description="profile / preference / fact")
    content: str
    importance: float
    created_at: datetime
    last_used_at: datetime | None = None


class ClearMemoryResponse(BaseModel):
    deleted: int = Field(description="本次清掉的记忆条数")
