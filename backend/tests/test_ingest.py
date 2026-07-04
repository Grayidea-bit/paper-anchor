import fitz
import pytest

from app.services.ingest import CHUNK_MAX_CHARS, parse_pdf


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
