import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError, NotFoundError
from app.llm import LLMError, chat_stream, embed_query
from app.services import rag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: str = "新對話"


class Selection(BaseModel):
    text: str = Field(max_length=4000)
    chunk_id: int | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    selection: Selection | None = None
    language: str | None = Field(default=None, pattern=r"^[A-Za-z-]{2,10}$")


@router.get("/documents/{doc_id}/conversations")
async def list_document_conversations(doc_id: int) -> list[dict]:
    async with SessionLocal() as session:
        if await repo.get_document(session, doc_id) is None:
            raise NotFoundError("document", doc_id)
        return await repo.list_conversations_scoped(
            session, scope="document", document_id=doc_id
        )


@router.post("/documents/{doc_id}/conversations", status_code=201)
async def create_document_conversation(doc_id: int, body: ConversationCreate) -> dict:
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
        if doc is None:
            raise NotFoundError("document", doc_id)
        if doc["status"] != "ready":
            raise AppError("not_ready", "文獻尚未處理完成")
        return await repo.create_conversation(
            session, scope="document", title=body.title, document_id=doc_id
        )


@router.get("/library/conversations")
async def list_library_conversations() -> list[dict]:
    async with SessionLocal() as session:
        return await repo.list_conversations_scoped(session, scope="library")


@router.post("/library/conversations", status_code=201)
async def create_library_conversation(body: ConversationCreate) -> dict:
    async with SessionLocal() as session:
        return await repo.create_conversation(session, scope="library", title=body.title)


@router.get("/conversations/{conv_id}/messages")
async def list_messages(conv_id: int) -> list[dict]:
    async with SessionLocal() as session:
        if await repo.get_conversation(session, conv_id) is None:
            raise NotFoundError("conversation", conv_id)
        return await repo.list_messages(session, conv_id)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: int, body: MessageCreate) -> StreamingResponse:
    """RAG 對話（docs/02 D3/D6）。依 conv.scope 決定檢索範圍。

    SSE：token* → citations → done | error。
    """
    async with SessionLocal() as session:
        conv = await repo.get_conversation(session, conv_id)
        if conv is None:
            raise NotFoundError("conversation", conv_id)
        scope = conv["scope"]
        if body.selection is not None and scope != "document":
            raise AppError("selection_not_allowed", "選取提問僅限單篇文獻對話")
        scope_title: str | None = None
        doc_id: int | None = None
        project_id: int | None = None
        if scope == "document":
            doc = await repo.get_document(session, conv["document_id"])
            if doc is None:
                raise NotFoundError("document", conv["document_id"])
            scope_title = doc["title"]
            doc_id = doc["id"]
        elif scope == "project":
            project = await repo.get_project(session, conv["project_id"])
            if project is None:
                raise NotFoundError("project", conv["project_id"])
            scope_title = project["name"]
            project_id = project["id"]
        history = await repo.list_messages(session, conv_id)

    async def stream():
        try:
            async with SessionLocal() as session:
                selection = body.selection.model_dump() if body.selection else None
                await repo.add_message(
                    session, conv_id, "user", body.content, selection=selection
                )
                query_embedding = await embed_query(body.content)
                context = await rag.retrieve_context(
                    session,
                    query_embedding,
                    scope=scope,
                    doc_id=doc_id,
                    project_id=project_id,
                    selection_chunk_id=body.selection.chunk_id if body.selection else None,
                )
            messages = rag.build_messages(
                context,
                history,
                body.content,
                scope=scope,
                scope_title=scope_title,
                selection_text=body.selection.text if body.selection else None,
                language=body.language,
            )
            parts: list[str] = []
            usage: dict = {}
            async for event in chat_stream(messages):
                if event["type"] == "token":
                    parts.append(event["text"])
                    yield _sse("token", {"text": event["text"]})
                elif event["type"] == "usage":
                    usage = {
                        "prompt_tokens": event["prompt_tokens"],
                        "completion_tokens": event["completion_tokens"],
                    }
            answer = "".join(parts)
            if not answer.strip():
                # 推理模型可能把 token 預算全花在思考段（finish=length）
                msg = "模型沒有產出答案（思考超出長度限制），請重問或換個問法"
                yield _sse("error", {"message": msg})
                return
            citations = rag.parse_citations(answer, context)
            yield _sse("citations", {"citations": citations})
            async with SessionLocal() as session:
                saved = await repo.add_message(
                    session, conv_id, "assistant", answer,
                    citations=citations, token_usage=usage,
                )
            yield _sse("done", {"message_id": saved["id"], "token_usage": usage})
        except LLMError as e:
            logger.exception("chat failed: conv=%s", conv_id)
            yield _sse("error", {"message": f"LLM 呼叫失敗：{e}"})
        except Exception:
            logger.exception("chat failed: conv=%s", conv_id)
            yield _sse("error", {"message": "系統錯誤，請稍後再試"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
