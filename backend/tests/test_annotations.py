"""標註 CRUD 測試（T-AN-01）- 以 repo 函式單元測試為主。"""

import pytest

from app.db import repo


@pytest.mark.asyncio
async def test_create_annotation(test_db):
    """建立標註。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        ann = await repo.create_annotation(
            session,
            1,
            type="underline",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
            selected_text="test text",
        )
    assert ann["type"] == "underline"
    assert ann["color"] == "amber"
    assert ann["page"] == 1
    assert ann["selected_text"] == "test text"
    assert "id" in ann


@pytest.mark.asyncio
async def test_create_annotation_with_chunk_id(test_db):
    """建立標註並指定 chunk_id。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        ann = await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="terracotta",
            page=2,
            bbox_list=[[15, 25, 95, 35]],
            chunk_id=1,
        )
    assert ann["chunk_id"] == 1


@pytest.mark.asyncio
async def test_list_annotations_empty(test_db):
    """列出空標註列表。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        anns = await repo.list_annotations(session, 1)
    assert anns == []


@pytest.mark.asyncio
async def test_list_annotations_multiple(test_db):
    """列出多個標註。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        # 建立 3 個標註
        for i in range(3):
            await repo.create_annotation(
                session,
                1,
                type="highlight",
                color="amber",
                page=1,
                bbox_list=[[10 * i, 20, 100 * (i + 1), 30]],
            )
        anns = await repo.list_annotations(session, 1)
    assert len(anns) == 3
    assert all(ann["page"] == 1 for ann in anns)


@pytest.mark.asyncio
async def test_update_annotation_note_text(test_db):
    """更新標註的 note_text。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        ann = await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
            note_text="old note",
        )
        ann_id = ann["id"]
        updated = await repo.update_annotation(session, ann_id, note_text="new note")
    assert updated["note_text"] == "new note"
    assert updated["color"] == "amber"  # 未更改


@pytest.mark.asyncio
async def test_update_annotation_color(test_db):
    """更新標註的 color。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        ann = await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
        )
        ann_id = ann["id"]
        updated = await repo.update_annotation(session, ann_id, color="sage")
    assert updated["color"] == "sage"


@pytest.mark.asyncio
async def test_update_annotation_not_found(test_db):
    """更新不存在的標註回 None。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        result = await repo.update_annotation(session, 999999, note_text="test")
    assert result is None


@pytest.mark.asyncio
async def test_delete_annotation(test_db):
    """刪除標註。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        ann = await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
        )
        ann_id = ann["id"]
        deleted = await repo.delete_annotation(session, ann_id)
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_annotation_not_found(test_db):
    """刪除不存在的標註回 False。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        result = await repo.delete_annotation(session, 999999)
    assert result is False


@pytest.mark.asyncio
async def test_list_annotations_scoped_no_filter(test_db):
    """查詢所有標註（無範圍限制）。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
        )
        anns = await repo.list_annotations_scoped(session)
    assert len(anns) == 1


@pytest.mark.asyncio
async def test_list_annotations_scoped_by_document(test_db):
    """按文獻 ID 查詢標註。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
        )
        anns = await repo.list_annotations_scoped(session, document_id=1)
    assert len(anns) == 1
    assert anns[0]["document_id"] == 1


@pytest.mark.asyncio
async def test_list_annotations_scoped_by_type(test_db):
    """按類型過濾標註。"""
    session_maker, _ = test_db
    async with session_maker() as session:
        await repo.create_annotation(
            session,
            1,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[10, 20, 100, 30]],
        )
        await repo.create_annotation(
            session,
            1,
            type="note",
            color="sage",
            page=1,
            bbox_list=[[30, 40, 70, 50]],
        )
        anns = await repo.list_annotations_scoped(session, type_filter="highlight")
    assert len(anns) == 1
    assert anns[0]["type"] == "highlight"
