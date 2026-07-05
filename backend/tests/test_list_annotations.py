"""list_annotations AI 工具測試（T-AN-06）。"""

import json
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.db import repo
from app.tools import ToolDeps
from app.tools.list_annotations import list_annotations


@dataclass
class FakeCtx:
    """輕量假 RunContext：list_annotations 只讀 ctx.deps。"""

    deps: ToolDeps


@pytest.fixture
async def two_docs(test_db):
    """建兩份文獻（各屬不同專案）、各一個 chunk，回傳其 id。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        await session.execute(
            text("INSERT INTO projects (id, user_id, name) VALUES (1, 1, 'Proj A')")
        )
        await session.execute(
            text("INSERT INTO projects (id, user_id, name) VALUES (2, 1, 'Proj B')")
        )
        doc_a = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, project_id, title, filename, file_path, page_count, status)
                    VALUES (1, 1, 'Paper A', 'a.pdf', '/tmp/a.pdf', 5, 'ready')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        doc_b = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, project_id, title, filename, file_path, page_count, status)
                    VALUES (1, 2, 'Paper B', 'b.pdf', '/tmp/b.pdf', 5, 'ready')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        chunk_a = (
            await session.execute(
                text(
                    """
                    INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
                    VALUES (:doc_id, 0, 3, 'method', 'Chunk A content', :bbox_list)
                    RETURNING id
                    """
                ),
                {"doc_id": doc_a, "bbox_list": json.dumps([[1, 2, 3, 4]])},
            )
        ).scalar()
        chunk_b = (
            await session.execute(
                text(
                    """
                    INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
                    VALUES (:doc_id, 0, 7, 'results', 'Chunk B content', :bbox_list)
                    RETURNING id
                    """
                ),
                {"doc_id": doc_b, "bbox_list": json.dumps([[5, 6, 7, 8]])},
            )
        ).scalar()
        await session.commit()
    return {"doc_a": doc_a, "doc_b": doc_b, "chunk_a": chunk_a, "chunk_b": chunk_b}


@pytest.fixture
async def seeded_annotations(test_db, two_docs):
    """兩份文獻各建幾筆標註：含有/無 chunk_id、有/無 note_text、三種 type。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        # 文獻 A：畫線（有 chunk_id）、底色（無 chunk_id，有 note）、註解（有 chunk_id + note）
        await repo.create_annotation(
            session,
            two_docs["doc_a"],
            type="underline",
            color="amber",
            page=3,
            bbox_list=[[1, 2, 3, 4]],
            chunk_id=two_docs["chunk_a"],
            selected_text="A 的畫線重點文字",
        )
        await repo.create_annotation(
            session,
            two_docs["doc_a"],
            type="highlight",
            color="terracotta",
            page=4,
            bbox_list=[[10, 20, 30, 40]],
            selected_text="A 的底色重點",
            note_text="這段很重要",
        )
        await repo.create_annotation(
            session,
            two_docs["doc_a"],
            type="note",
            color="sage",
            page=3,
            bbox_list=[[1, 2, 3, 4]],
            chunk_id=two_docs["chunk_a"],
            selected_text="被註解的原文",
            note_text="我的疑問",
        )
        # 文獻 B（不同專案）：底色，無 chunk_id
        await repo.create_annotation(
            session,
            two_docs["doc_b"],
            type="highlight",
            color="slate",
            page=7,
            bbox_list=[[5, 6, 7, 8]],
            chunk_id=two_docs["chunk_b"],
            selected_text="B 的底色重點",
        )
    return two_docs


@pytest.mark.asyncio
class TestListAnnotationsTool:
    async def test_document_scope_only_returns_that_document(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps))
        assert "Paper B" not in result.return_value
        assert result.return_value.count("《Paper A》") == 3

    async def test_project_scope_isolation(self, test_db, seeded_annotations):
        """範圍隔離鐵證：project scope 不得洩漏其他專案文獻的標註。"""
        session_maker, _ = test_db
        deps = ToolDeps(scope="project", doc_id=None, project_id=1)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps))
        assert "Paper A" in result.return_value
        assert "Paper B" not in result.return_value
        if result.metadata:
            doc_ids = {c["document_id"] for c in result.metadata["chunks"]}
            assert seeded_annotations["doc_b"] not in doc_ids

    async def test_project_scope_other_project(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="project", doc_id=None, project_id=2)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps))
        assert "Paper B" in result.return_value
        assert "Paper A" not in result.return_value

    async def test_type_filter_underline_only(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps), type_filter="underline")
        assert "畫線" in result.return_value
        assert "底色" not in result.return_value
        assert "註解" not in result.return_value
        assert "找到 1 筆標註" in result.return_value

    async def test_invalid_type_filter_falls_back_to_all(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps), type_filter="bogus")
        assert "找到 3 筆標註" in result.return_value

    async def test_chunk_citation_and_metadata_shape(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps))
        chunk_id = seeded_annotations["chunk_a"]
        assert f"[C{chunk_id}]" in result.return_value
        assert result.metadata is not None
        chunks = result.metadata["chunks"]
        # 去重：兩筆標註（underline + note）共用同一 chunk_id，只應出現一次
        ids = [c["id"] for c in chunks]
        assert ids.count(chunk_id) == 1
        chunk = chunks[0]
        assert "page" in chunk and "bbox_list" in chunk and "document_id" in chunk
        assert "chunk_index" in chunk and "content" in chunk and "document_title" in chunk

    async def test_note_text_appended(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps), type_filter="highlight")
        assert "使用者註：這段很重要" in result.return_value

    async def test_note_type_selected_text_is_annotated_original(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps), type_filter="note")
        assert "被註解的原文" in result.return_value
        assert "使用者註：我的疑問" in result.return_value

    async def test_no_annotations_returns_fixed_message(self, test_db, two_docs):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=two_docs["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps))
        assert result.return_value == "目前範圍內沒有任何標註。"
        assert result.metadata is None

    async def test_library_scope_returns_all(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="library", doc_id=None, project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps))
        assert "Paper A" in result.return_value
        assert "Paper B" in result.return_value
        assert "找到 4 筆標註" in result.return_value

    async def test_max_results_clamped(self, test_db, seeded_annotations):
        session_maker, _ = test_db
        deps = ToolDeps(scope="document", doc_id=seeded_annotations["doc_a"], project_id=None)
        with patch("app.tools.list_annotations.SessionLocal", session_maker):
            result = await list_annotations(FakeCtx(deps=deps), max_results=0)
        # clamp 到 1 筆
        assert "找到 1 筆標註" in result.return_value


class TestListAnnotationsRegistered:
    def test_registered_in_tool_list(self):
        from app import tools

        tools.reset_cache()
        names = [t["name"] for t in tools.list_tools()]
        assert "list_annotations" in names
