"""
向量化客户端（embedding）。

为什么单独一个文件：
  P2 有两处要用向量——知识库切片和长期记忆。如果各自写一份 HTTP 调用，
  重试策略、超时、维度校验会变成两份慢慢跑偏的实现。

为什么直接发 HTTP 而不套 openai SDK：
  embedding 接口只有一个入参两个字段，用 httpx 反而少一层抽象。
  更重要的是我要精确控制"失败降级"的边界，套 SDK 之后就得同时处理
  SDK 自己的异常层级和它包装过的 HTTP 状态码，反而更绕。

为什么失败要降级而不是往上抛：
  对话主链路是"模型 + 数据库"，向量化只是增强。
  向量服务挂了，用户至少还能正常聊天，只是这次检索不到资料、记忆退回按重要度取。
  把异常抛给用户，等于让一个可选依赖的单点故障变成整个产品不可用。

为什么要做「冷却」：
  没配 Key 或者服务挂了的时候，每轮对话都去试一次，就要每轮等一次超时。
  失败后一段时间内直接判定不可用，靠时间自愈，比在业务代码里到处加判断干净。
"""

import asyncio
import logging
import time
from collections.abc import Sequence

import httpx

from config import settings

logger = logging.getLogger(__name__)

# 失败后的冷却时长（秒）。期间 available 直接返回 False，不再发起请求。
_COOLDOWN_SECONDS = 60
# 单次请求最多重试几次（只对 429 / 5xx 这类"再试可能就好了"的错误重试）
_MAX_RETRIES = 3


class EmbeddingError(RuntimeError):
    """向量化失败。上层 catch 它然后降级，不要把异常漏给用户。"""


class EmbeddingUnavailable(EmbeddingError):
    """未配置或处于冷却期。属于"预期内"的不可用，不记 error 日志。"""


class EmbeddingClient:
    """
    全局唯一的向量化客户端。

    惰性创建 httpx.AsyncClient 的原因：
      模块导入时创建会把连接池绑定到一个还没运行的事件循环上，
      在 uvicorn --reload 的热重载场景里会报 "Event loop is closed"。
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._cooldown_until = 0.0
        # 维度不一致只提示一次，避免刷屏。换模型时这个提示是关键线索。
        self._dim_warned = False

    # ------------------------------------------------------------------ 状态
    @property
    def configured(self) -> bool:
        """是否配了 Key。没配就是一个"永远不可用"的客户端。"""
        return bool(settings.EMBED_API_KEY.strip()) and settings.RAG_ENABLED

    @property
    def available(self) -> bool:
        """当前这一刻能不能用：配了 Key 且不在冷却期。"""
        if not self.configured:
            return False
        return time.monotonic() >= self._cooldown_until

    def _enter_cooldown(self, reason: str) -> None:
        self._cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        logger.warning("向量服务不可用，%.0f 秒内跳过调用：%s", _COOLDOWN_SECONDS, reason)

    # ------------------------------------------------------------------ 请求
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=settings.EMBED_BASE_URL.rstrip("/"),
                timeout=httpx.Timeout(settings.EMBED_TIMEOUT),
                headers={
                    "Authorization": f"Bearer {settings.EMBED_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def _post_batch(self, batch: list[str]) -> list[list[float]]:
        """发一批文本，返回等长的向量列表。带重试和维度校验。"""
        payload = {"model": settings.EMBED_MODEL, "input": batch, "encoding_format": "float"}
        # 只有配置了才传 dimensions：不支持的模型多传字段会直接 400
        if settings.EMBED_DIMENSIONS > 0:
            payload["dimensions"] = settings.EMBED_DIMENSIONS

        last_error = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._get_client().post("/embeddings", json=payload)
            except httpx.HTTPError as exc:
                # 网络层问题（DNS / 连接被重置 / 超时）：值得重试
                last_error = f"网络错误：{exc}"
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                self._enter_cooldown(last_error)
                raise EmbeddingError(last_error) from exc

            if response.status_code == 200:
                return self._parse(response.json(), expected=len(batch))

            # 401 是 Key 错，重试没有意义，直接整体失效
            if response.status_code in (401, 403):
                self._enter_cooldown(f"鉴权失败 HTTP {response.status_code}")
                raise EmbeddingError(
                    "向量服务鉴权失败：EMBED_API_KEY 无效或未实名认证"
                )

            # 429 限流、5xx 服务端抖动：退避后重试
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}：{response.text[:200]}"
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                self._enter_cooldown(last_error)
                raise EmbeddingError(last_error)

            # 402 是账户额度问题。
            # 单独拎出来翻译，因为原始返回是一串 {"code":30001,"message":"Sorry,
            # your account balance is insufficient"}，用户从这行里看不出该干什么，
            # 而且很容易误判成"代码写错了"去改请求体。
            # 实测：服务商页面上标注"免费"的模型，同样会受账户额度闸门约束，
            # 余额为 0 时连一次调用都发不出去。
            if response.status_code == 402:
                self._enter_cooldown("账户额度不足（HTTP 402）")
                raise EmbeddingError(
                    "向量服务拒绝调用：账户余额或免费额度不足（HTTP 402）。"
                    "请给 EMBED_API_KEY 对应的账户充值 / 完成实名认证，"
                    "或改配 EMBED_BASE_URL 与 EMBED_MODEL 换一家服务商。"
                )

            # 其余（400 模型名错、404 路径错）是配置问题，重试也不会好
            detail = response.text[:300]
            self._enter_cooldown(f"HTTP {response.status_code}")
            raise EmbeddingError(f"向量服务返回 {response.status_code}：{detail}")

        raise EmbeddingError(last_error or "向量服务调用失败")

    def _parse(self, body: dict, *, expected: int) -> list[list[float]]:
        """
        解析响应，并按 index 排序。

        为什么要显式排序：OpenAI 兼容实现并不保证返回顺序与输入一致，
        一旦错位，切片和向量就会对错号，检索结果看起来"能跑但全是乱的"，
        排查成本极高。宁可多写这一行。
        """
        try:
            items = sorted(body["data"], key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"向量响应结构异常：{str(body)[:300]}") from exc

        if len(vectors) != expected:
            raise EmbeddingError(f"向量条数不符：期望 {expected}，实际 {len(vectors)}")

        # 维度必须和数据库列一致，否则插库会报 "expected 1024 dimensions"
        actual_dim = len(vectors[0]) if vectors else 0
        if actual_dim and actual_dim != settings.EMBED_DIM and not self._dim_warned:
            self._dim_warned = True
            logger.error(
                "向量维度不一致：模型 %s 输出 %s 维，配置 EMBED_DIM=%s。"
                "换模型时必须同步改 EMBED_DIM 并重建 chunks 表的向量列。",
                settings.EMBED_MODEL,
                actual_dim,
                settings.EMBED_DIM,
            )
            raise EmbeddingError(
                f"维度不匹配：模型输出 {actual_dim} 维，配置是 {settings.EMBED_DIM} 维"
            )

        return vectors

    # ------------------------------------------------------------------ 对外
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """
        批量向量化。按 EMBED_BATCH_SIZE 分组顺序发送。

        为什么顺序而不是并发：免费额度都有 RPM 限制，一股脑并发更容易撞 429，
        而这里的调用量（几十到几百段）根本没到需要并发的量级。
        """
        items = [text for text in texts if text and text.strip()]
        if not items:
            return []
        if not self.available:
            raise EmbeddingUnavailable("向量服务未配置或处于冷却期")

        size = max(1, settings.EMBED_BATCH_SIZE)
        vectors: list[list[float]] = []
        for start in range(0, len(items), size):
            vectors.extend(await self._post_batch(items[start : start + size]))
        return vectors

    async def embed_one(self, text: str) -> list[float] | None:
        """
        单条向量化，失败返回 None 而不是抛异常。

        这是"检索路径"专用的宽松接口：检索失败就当没检索到，
        不能让一次 embedding 抖动把整轮对话打断。
        写入路径（上传文档）用的是 embed()，那里的失败需要显式记录到任务状态。
        """
        if not text or not text.strip() or not self.available:
            return None
        try:
            vectors = await self.embed([text])
        except EmbeddingError as exc:
            logger.warning("查询向量化失败，本次检索跳过：%s", exc)
            return None
        return vectors[0] if vectors else None

    async def close(self) -> None:
        """应用关闭时释放连接池。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


# 全局单例，用法与 llm 一致
embeddings = EmbeddingClient()
