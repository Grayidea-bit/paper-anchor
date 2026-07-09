import fitz
import pytest
from sqlalchemy import text

from app.db import repo
from app.services import ingest as ingest_module
from app.services.ingest import CHUNK_MAX_CHARS, ingest_document, parse_pdf


@pytest.fixture
def sample_pdf(tmp_path):
    """兩頁測試 PDF：大標題 + 多段內文。"""
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 80), "A Study of Testing Pipelines", fontsize=20)
    y = 140
    for i in range(12):
        page1.insert_text((72, y), f"Paragraph {i} on page one. " * 8, fontsize=11)
        y += 40
    page2 = doc.new_page()
    page2.insert_text((72, 80), "Second page content. " * 10, fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_parse_pdf_extracts_title_and_pages(sample_pdf):
    title, page_count, chunks = parse_pdf(sample_pdf)
    assert title == "A Study of Testing Pipelines"
    assert page_count == 2
    assert len(chunks) >= 2


def test_chunks_carry_citation_anchor_fields(sample_pdf):
    """引用錨點資訊鏈起點（CLAUDE.md 鐵律 1）：page 與 bbox 必須齊全。"""
    _, _, chunks = parse_pdf(sample_pdf)
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c["page"] >= 1
        assert len(c["content"]) <= CHUNK_MAX_CHARS + 1
        assert c["bbox_list"], "每個 chunk 必須至少有一個 bbox"
        for bbox in c["bbox_list"]:
            x0, y0, x1, y1 = bbox
            assert x1 > x0 and y1 > y0


def test_chunks_do_not_cross_pages(sample_pdf):
    _, _, chunks = parse_pdf(sample_pdf)
    pages = [c["page"] for c in chunks]
    assert pages == sorted(pages)
    assert set(pages) == {1, 2}


def test_scanned_pdf_rejected(tmp_path):
    path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    with pytest.raises(ValueError, match="掃描版"):
        parse_pdf(str(path))


# ---------------------------------------------------------------------------
# 冪等重跑（M15 T-FD-01）：ingest_document 開頭無條件 delete_chunks，
# 重跑（reingest 端點 / restore 修復 / 啟動重置後手動重試）不撞
# UNIQUE(document_id, chunk_index)、不留新舊 chunk 混雜的半殘狀態。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_ingest_document_survives_stale_chunk_same_index(test_db, monkeypatch):
    """先種一顆殘留（chunk_index=0）的舊 chunk 再跑 ingest_document：不得拋
    IntegrityError，且結束時只留下這次解析出的新 chunk。"""
    session_maker, _ = test_db
    monkeypatch.setattr(ingest_module, "SessionLocal", session_maker)

    async with session_maker() as s:
        doc_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, '', 'doc.pdf', '/tmp/doc.pdf', 0, 'failed')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        await s.execute(
            text(
                """
                INSERT INTO chunks (document_id, chunk_index, page, content, bbox_list)
                VALUES (:d, 0, 1, 'stale leftover', '[]')
                """
            ),
            {"d": doc_id},
        )
        await s.commit()

    def fake_parse_pdf(path: str) -> tuple[str, int, list[dict]]:
        return (
            "New Title",
            1,
            [
                {
                    "chunk_index": 0,
                    "page": 1,
                    "section": None,
                    "content": "fresh content",
                    "bbox_list": [[0.0, 0.0, 10.0, 10.0]],
                }
            ],
        )

    async def fake_embed_passages(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    async def fake_update_chunk_embeddings(session, chunk_ids, embeddings) -> None:
        return None  # 真實 pgvector CAST 語法非 SQLite 相容，測試不驗證這段

    monkeypatch.setattr(ingest_module, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(ingest_module, "embed_passages", fake_embed_passages)
    monkeypatch.setattr(repo, "update_chunk_embeddings", fake_update_chunk_embeddings)

    await ingest_document(doc_id, run_digest=False)  # 不應拋 UNIQUE IntegrityError

    async with session_maker() as s:
        doc_row = (
            await s.execute(text("SELECT status FROM documents WHERE id = :d"), {"d": doc_id})
        ).one()
        chunk_rows = (
            await s.execute(
                text("SELECT chunk_index, content FROM chunks WHERE document_id = :d"),
                {"d": doc_id},
            )
        ).all()

    assert doc_row.status == "ready"
    assert [(r.chunk_index, r.content) for r in chunk_rows] == [(0, "fresh content")]
