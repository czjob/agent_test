"""
知识库接口：上传、列表、删除、重新索引。

只做 HTTP 层的事：接文件、校验、调度后台任务、返回状态。
文本解析和向量化都在 services/knowledge.py。

一个刻意的设计：上传接口不等待向量化完成。
  一份 PDF 切出几十段、每段都要调一次 embedding，全程十几秒。
  同步等待会让请求长时间挂起（还可能被网关超时掐断），用户看到的是一个转不停的圈。
  所以接口立刻返回 status=pending，前端轮询列表看状态变 ready。
  这也是"有状态的后台任务"最朴素的做法——不需要 WebSocket，一次轮询就够。
"""

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from background import spawn
from db import get_db
from embeddings import embeddings
from models import User
from schemas import DocumentOut, ReindexResponse
from security import get_current_user
from services import knowledge as kb_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["知识库"])


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(..., description="支持 txt / md / pdf"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    """
    上传文档。返回时文档已落库，但还没向量化完（status=pending）。

    为什么在入口就拦"没配 Key"：
      否则用户能上传成功、状态一路 pending 然后 failed，
      错误信息藏在文档的 error 字段里，很难联想到是服务端没配向量模型。
      在这里直接 503 + 一句人话，是成本最低的沟通方式。
    """
    if not embeddings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="向量服务未配置：请在 backend/.env 填写 EMBED_API_KEY 后重启后端",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    raw = await file.read()
    try:
        document = await kb_service.create_document(
            db, current_user.id, file.filename, raw
        )
    except kb_service.DocumentError as exc:
        # 解析失败 / 类型不支持 / 太大，都属于用户输入问题，400 比 500 准确
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 切分和向量化丢到后台。接口不等它，前端靠轮询状态感知进度
    spawn(
        kb_service.process_document(document.id),
        name=f"index-doc:{document.id}",
    )
    return DocumentOut.model_validate(document)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentOut]:
    """当前用户的知识库列表，按上传时间倒序。"""
    rows = await kb_service.list_documents(db, current_user.id)
    return [DocumentOut.model_validate(row) for row in rows]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    删除文档，切片级联删除。

    不存在和不属于当前用户都返回 404，理由同会话接口：
    403 等于确认"这个 id 存在，只是不是你的"。
    """
    deleted = await kb_service.delete_document(db, current_user.id, document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")


@router.post("/{document_id}/reindex", response_model=ReindexResponse)
async def reindex_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReindexResponse:
    """
    重新切分并向量化。换 embedding 模型、或调整 CHUNK_SIZE 之后用这个。

    之所以要有这条接口：向量是跟着模型走的，换模型后旧向量和新的不可比。
    没有重跑入口的话，用户只能删了重传——而他未必还留着原文件。
    全文一直存在 documents.content 里，就是为了这一刻。
    """
    if not embeddings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="向量服务未配置：请在 backend/.env 填写 EMBED_API_KEY 后重启后端",
        )

    document = await kb_service.reset_for_reindex(db, current_user.id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    spawn(kb_service.process_document(document.id), name=f"reindex-doc:{document.id}")
    return ReindexResponse(id=document.id, status=document.status)
