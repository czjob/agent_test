"""
知识库服务：文本解析 → 切分 → 向量化 → 检索。

分层：
  routers/documents.py  只做 HTTP 的事（收文件、返回状态、鉴权）
  services/knowledge.py 负责"一份文档怎么变成可检索的向量"（本文件）
  embeddings.py         只负责"一段文本怎么变成一串数字"

分开的原因很实际：切分策略是最容易反复调的部分（500 字还是 800 字、重叠多少），
如果它和 HTTP 层揉在一起，每次调参都要过一遍接口逻辑。现在改 split_text 就能生效。

为什么切片和向量化都放后台：
  embedding 是网络调用，一批 16 段差不多几百毫秒，一份 PDF 几十批就是十几秒。
  放在上传请求里同步等待，浏览器的上传进度条会转很久，用户以为卡死了。
  所以上传接口只做"解析 + 落库 + 返回 pending"，向量化交给后台任务，前端轮询状态。
"""

import io
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import SessionLocal
from embeddings import EmbeddingError, embeddings
from models import Chunk, Document

logger = logging.getLogger(__name__)

# 支持的扩展名。加新格式只需要在 parse_document 里加一个分支 + 这里补一项
SUPPORTED_EXTS = (".txt", ".md", ".markdown", ".pdf")

# 中文句子结束符。用来在"段落太长"时找一个语义边界断开
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;\n])")

# Markdown 标题行。切分时把它当成硬边界——见 split_text 里的说明
_HEADING = re.compile(r"^#{1,6}\s")
# 整块内容只有一行标题（没有正文），这种块不该单独成切片
_HEADING_ONLY = re.compile(r"^#{1,6}\s[^\n]*$")


class DocumentError(ValueError):
    """文档处理相关的可预期错误。router 层捕获后转成 400。"""


class UnsupportedFileType(DocumentError):
    pass


class DocumentTooLarge(DocumentError):
    pass


# ====================== 文本解析 ======================
def normalize_text(text: str) -> str:
    """
    统一换行、去掉零宽字符和行尾空格。

    为什么值得单独做一遍：
      PDF 抽出来的文本经常带 \\r\\n、全角空格、零宽连接符。
      这些东西不影响阅读，但会让切分的长度计算和检索的相似度都偏一点，
      而且肉眼完全看不出来——属于典型的"先洗掉省事"。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\ufeff", "").replace("\u200b", "")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _decode_text(raw: bytes) -> str:
    """
    纯文本解码。依次尝试 UTF-8、GBK、Latin-1。

    为什么要试 GBK：Windows 上用户用记事本存的 txt 默认就是 GBK，
    直接 UTF-8 解会得到一堆乱码而不是报错，比报错更难发现。
    """
    for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes) -> str:
    """PDF 取文字。pypdf 是可选依赖，没装时给出可执行的提示而不是 ImportError 栈。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise UnsupportedFileType(
            "服务端未安装 pypdf，暂不支持 PDF。执行 pip install pypdf 后重启即可。"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise DocumentError(f"PDF 解析失败：{exc}") from exc

    # 扫描版 PDF 抽不出文字，pages 会是全空。这时要给出和"解析失败"不同的提示，
    # 否则用户会一直重试同一份文件
    text = "\n\n".join(page for page in pages if page)
    if not text:
        raise DocumentError("这份 PDF 里没有可提取的文字（可能是扫描件），请改用 txt / md")
    return text


def parse_document(filename: str, raw: bytes) -> str:
    """按扩展名解析出纯文本。"""
    ext = Path(filename).suffix.lower()
    if ext in (".txt", ".md", ".markdown"):
        return normalize_text(_decode_text(raw))
    if ext == ".pdf":
        return normalize_text(_extract_pdf(raw))
    raise UnsupportedFileType(
        f"不支持的文件类型 {ext or '（无扩展名）'}，目前支持：{'、'.join(SUPPORTED_EXTS)}"
    )


# ====================== 切分 ======================
def _split_long_paragraph(paragraph: str, size: int) -> list[str]:
    """
    把超长段落按句子边界拆到 size 以内。

    兜底逻辑：如果一句话本身就超过 size（比如一整段没有标点的代码或表格），
    只能按字符硬切——这是唯一会破坏语义的情况，但总比让一个切片撑爆上下文好。
    """
    if len(paragraph) <= size:
        return [paragraph]

    pieces: list[str] = []
    buffer = ""
    for sentence in (s for s in _SENTENCE_END.split(paragraph) if s.strip()):
        if len(sentence) > size:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(sentence[i : i + size] for i in range(0, len(sentence), size))
            continue
        if not buffer:
            buffer = sentence
        elif len(buffer) + len(sentence) <= size:
            buffer += sentence
        else:
            pieces.append(buffer)
            buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def split_text(text: str) -> list[str]:
    """
    按段落聚合切分，相邻切片之间保留 overlap 个字符的重叠。

    为什么不用固定长度硬切：
      中文没有空格分词，按 500 字符硬切十有八九从句子中间断开。
      检索出来给模型看的片段开头半句话，它会当成完整信息用。
      按空行分段再聚合，边界落在语义单位上，读起来才通顺。

    为什么需要重叠：
      答案刚好横跨两个切片时，任何一个切片单独看都不完整。
      重叠一段让"答案的尾巴"和"答案的开头"都能被某个切片完整覆盖。
      重叠不能设太大，否则同一段内容会挤掉 top-k 的名额。

    还有一处不靠长度做决策：Markdown 标题。
      段落聚合和重叠都在"长度够不够"这个维度上衡量，
      但一份笔记里最可靠的分界其实是作者写的标题。
      标题所在处无条件断开（见下面循环里的说明），
      这样每个切片天然带着自己的小标题，向量里就带上了"这段讲什么"的信息。
    """
    size = max(120, settings.CHUNK_SIZE)
    overlap = max(0, min(settings.CHUNK_OVERLAP, size // 2))

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    pieces: list[str] = []
    for paragraph in paragraphs:
        pieces.extend(_split_long_paragraph(paragraph, size))

    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        if not buffer:
            buffer = piece
            continue
        # 遇到 Markdown 标题就在此处强制断开。
        #
        # 起因是实测：一份 491 字、含四个小节（构建优化 / 微前端 / 红烧肉 / 连接池）的笔记，
        # 因为没超过 CHUNK_SIZE 被聚合成了一整段。结果问"红烧肉怎么做"时，
        # 召回的是包含微前端和连接池的整篇，模型得自己从噪音里挑，
        # 而且这一个切片会占掉 top-k 的整整一个名额。
        #
        # 标题是作者亲手划的语义边界，比按字数切准得多，不用白不用。
        # 注意这里不加重叠：跨章节的重叠没有意义，反而会把上一节的内容
        # 掺进下一节的向量里，让两个主题的向量互相污染。
        if _HEADING.match(piece):
            # 但"只有一行标题、没有正文"的块不能单独成块。
            # 典型场景是文档开头的 `# 个人技术笔记`——8 个字，
            # 检索时几乎不可能被召回，白白占掉 top-k 一个名额；
            # 万一被召回也只是给模型一句标题，没有任何可用信息。
            # 把它当上下文前缀并进下一节，标题里的文档主题反而被带进去了。
            if _HEADING_ONLY.match(buffer.strip()):
                buffer = f"{buffer}\n\n{piece}"
            else:
                chunks.append(buffer)
                buffer = piece
            continue
        # +2 是拼接时插入的 "\n\n"
        if len(buffer) + len(piece) + 2 <= size:
            buffer = f"{buffer}\n\n{piece}"
            continue

        chunks.append(buffer)
        # 下一块的开头带上上一块的尾巴。留够 room 保证拼完不超 size
        room = size - len(piece) - 2
        tail = buffer[-min(overlap, room):] if overlap > 0 and room > 0 else ""
        buffer = f"{tail}\n\n{piece}" if tail else piece

    if buffer.strip():
        chunks.append(buffer)
    return chunks


# ====================== 文档 CRUD ======================
async def create_document(
    db: AsyncSession, user_id: uuid.UUID, filename: str, raw: bytes
) -> Document:
    """
    解析并落库，状态为 pending。真正的切分和向量化在 process_document 里做。

    尺寸检查和正文截断都在这里完成，保证后面任何环节拿到的都是"已收敛"的数据。
    """
    if len(raw) > settings.UPLOAD_MAX_BYTES:
        raise DocumentTooLarge(
            f"文件超过 {settings.UPLOAD_MAX_BYTES // (1024 * 1024)}MB 上限"
        )

    text = parse_document(filename, raw)
    if not text.strip():
        raise DocumentError("文件内容为空")

    # 超长文档截断而不是拒收：用户上传一份大文档，通常也只想让它"能被检索到"，
    # 直接拒绝的体验更差。截断后的字数会在列表里显示，用户自己看得出来。
    if len(text) > settings.DOCUMENT_MAX_CHARS:
        text = text[: settings.DOCUMENT_MAX_CHARS]

    document = Document(
        user_id=user_id,
        filename=Path(filename).name[:255],
        ext=Path(filename).suffix.lower(),
        size_bytes=len(raw),
        content=text,
        char_count=len(text),
        status="pending",
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def process_document(document_id: uuid.UUID) -> None:
    """
    后台任务：切分 + 向量化 + 落库。

    幂等设计：进来先清空该文档已有的切片。
      "重新索引"和"上次失败后重试"都会走到这条路径，不清空就会写出重复切片，
      检索时同一段内容占好几个名额，等于把 top-k 浪费掉了。
    """
    try:
        async with SessionLocal() as db:
            # 用条件 UPDATE 抢占，而不是"先读再判断再写"。
            #
            # 为什么：用户可能连点两次"重新索引"，或者上传接口和 reindex 撞在一起。
            # 两个任务同时干活的话，后一个会把前一个刚插入的切片删掉，
            # 结果是数据残缺但状态显示 ready——比直接失败更难查。
            # UPDATE ... WHERE status='pending' 是原子的，只有一个任务能拿到 rowcount=1。
            claimed = await db.execute(
                update(Document)
                .where(Document.id == document_id, Document.status == "pending")
                .values(status="processing", error=None)
            )
            await db.commit()
            if (claimed.rowcount or 0) == 0:
                return

            document = await db.get(Document, document_id)
            if document is None:
                return

            await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await db.commit()

            chunks = split_text(document.content)
            if not chunks:
                document.status = "failed"
                document.error = "切分后没有内容"
                await db.commit()
                return

            document.chunk_count = len(chunks)
            await db.commit()

            batch_size = max(1, settings.EMBED_BATCH_SIZE)
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                # 这里用严格版本的 embed()：失败要让整个文档标 failed，
                # 而不是像检索那样静默降级——用户需要知道"这份文件没进去"
                vectors = await embeddings.embed(batch)

                db.add_all(
                    Chunk(
                        document_id=document_id,
                        user_id=document.user_id,
                        seq=start + offset,
                        content=content,
                        embedding=vector,
                    )
                    for offset, (content, vector) in enumerate(zip(batch, vectors))
                )
                # 分批提交：一份长文档几百段，全部攒在一个事务里会长时间占着连接，
                # 中途失败还得整体回滚重来
                await db.commit()

            document.status = "ready"
            await db.commit()
            logger.info("文档 %s 向量化完成：%s 段", document_id, len(chunks))

    except EmbeddingError as exc:
        await _mark_failed(document_id, f"向量化失败：{exc}")
    except Exception as exc:  # pragma: no cover - 兜底，不让后台任务静默死掉
        logger.exception("文档处理失败：%s", document_id)
        await _mark_failed(document_id, f"处理失败：{exc}")


async def _mark_failed(document_id: uuid.UUID, reason: str) -> None:
    """
    把失败原因写回文档，前端能直接展示，用户不必去看服务端日志。

    顺带把 chunk_count 改成"真实落库的段数"：
      process_document 在开始向量化之前就把 chunk_count 设成了"计划切几段"，
      这样前端能在处理过程中显示进度。但一旦失败，这个数字就是假的——
      界面会显示"失败 · 1 段"，而库里其实一条都没有。
      失败时以实际行数为准，避免这种自相矛盾的状态。
    """
    try:
        async with SessionLocal() as db:
            document = await db.get(Document, document_id)
            if document is None:
                return
            actual = await db.scalar(
                select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
            )
            document.chunk_count = actual or 0
            document.status = "failed"
            document.error = reason[:500]
            await db.commit()
    except Exception:  # pragma: no cover
        logger.exception("标记文档失败状态时出错：%s", document_id)


async def list_documents(db: AsyncSession, user_id: uuid.UUID) -> list[Document]:
    """知识库列表。按创建时间倒序，最新的在最上面。"""
    stmt = (
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
    )
    return list((await db.scalars(stmt)).all())


async def get_document(
    db: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID
) -> Document | None:
    """按 id 取，user_id 写在 WHERE 里——越权防线和会话接口保持一致。"""
    return await db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )


async def delete_document(
    db: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID
) -> bool:
    """
    物理删除（切片随外键级联删除）。

    这里不做软删除，和记忆的处理相反：
      记忆必须软删，是因为"抽取任务每轮都跑，删了会被重新写回来"；
      文档是用户主动上传又主动删除的，没有任何流程会自动重建它，不存在复活问题。
    """
    result = await db.execute(
        delete(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def reset_for_reindex(
    db: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID
) -> Document | None:
    """把文档状态改回 pending，供后台重新切分向量化（换模型 / 调切分参数后使用）。"""
    document = await get_document(db, user_id, document_id)
    if document is None:
        return None
    document.status = "pending"
    document.error = None
    document.chunk_count = 0
    await db.commit()
    await db.refresh(document)
    return document


# ====================== 检索 ======================
@dataclass
class SearchHit:
    """一条检索命中。字段是给 tools.py 拼给模型看的结果用的。"""

    filename: str
    seq: int
    score: float
    content: str


async def search(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[SearchHit]:
    """
    语义检索：把 query 向量化，按余弦距离取最近的 k 段。

    为什么相似度阈值在 Python 里过滤，而不写进 SQL：
      HNSW 索引擅长的是"ORDER BY 距离 LIMIT k"，它按图逐层逼近最近邻。
      如果在 WHERE 里再加一个距离范围条件，优化器可能放弃索引改走顺序扫描
      （数据量小时看不出差别，文档多了就是几百毫秒和几十毫秒的差距）。
      先取 k 个再筛掉低分，召回率略低一点点，但行为可预期。

    为什么 distance 要单独成一列再算分数：
      pgvector 的 <=> 返回的是余弦距离（0 最相似，2 最不相似），
      而人对"相似度"的直觉是 1 最相似。score = 1 - distance 换算一下，
      阈值配置和给模型看的数字都用这个口径，不会出现"0.8 到底是好还是差"的困惑。
    """
    if not embeddings.available:
        return []

    vector = await embeddings.embed_one(query)
    if vector is None:
        return []

    k = top_k or settings.RAG_TOP_K
    threshold = settings.RAG_MIN_SCORE if min_score is None else min_score

    distance = Chunk.embedding.cosine_distance(vector).label("distance")
    stmt = (
        select(Chunk.content, Chunk.seq, Document.filename, distance)
        # join 只为拿文件名（给模型标注出处），过滤条件仍然落在 chunks 上
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.user_id == user_id,
            Chunk.embedding.is_not(None),
            Document.status == "ready",
        )
        .order_by(distance)
        .limit(k)
    )

    rows = (await db.execute(stmt)).all()

    hits: list[SearchHit] = []
    for content, seq, filename, dist in rows:
        score = 1.0 - float(dist)
        if score < threshold:
            continue
        hits.append(SearchHit(filename=filename, seq=seq, score=score, content=content))
    return hits


async def count_ready_documents(db: AsyncSession, user_id: uuid.UUID) -> int:
    """已就绪的文档数。工具在"知识库为空"时给模型一句更准确的提示。"""
    return (
        await db.scalar(
            select(func.count(Document.id)).where(
                Document.user_id == user_id, Document.status == "ready"
            )
        )
        or 0
    )
