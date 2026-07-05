import pytest

from app.llm import ThinkFilter, extract_json
from app.services import rag
from app.services.rag import build_messages, parse_citations

CHUNKS = [
    {
        "id": 101,
        "chunk_index": 3,
        "page": 2,
        "content": "方法段",
        "bbox_list": [[1, 2, 3, 4]],
        "document_id": 7,
        "document_title": "Paper A",
    },
    {
        "id": 102,
        "chunk_index": 7,
        "page": 5,
        "content": "實驗段",
        "bbox_list": [[5, 6, 7, 8]],
        "document_id": 8,
        "document_title": "Paper B",
    },
]


class TestThinkFilter:
    def test_passthrough(self):
        f = ThinkFilter()
        assert f.feed("hello ") + f.feed("world") + f.flush() == "hello world"

    def test_removes_think_block(self):
        f = ThinkFilter()
        out = f.feed("<think>內心戲</think>答案是 42")
        assert out + f.flush() == "答案是 42"

    def test_tag_split_across_chunks(self):
        f = ThinkFilter()
        pieces = [f.feed("<th"), f.feed("ink>秘密"), f.feed("</thi"), f.feed("nk>公開")]
        out = "".join(pieces) + f.flush()
        assert out == "公開"

    def test_unclosed_think_discarded(self):
        f = ThinkFilter()
        out = f.feed("<think>只想不說") + f.flush()
        assert out == ""


class TestParseCitations:
    """引用標籤 = 全域 chunk id（docs/02 D6）。"""

    def test_extracts_known_ids_in_order(self):
        answer = "方法如上 [C101]，結果見 [C102][C101]，虛構 [C999] 忽略。"
        cites = parse_citations(answer, CHUNKS)
        assert [c["label"] for c in cites] == [101, 102]
        assert cites[0]["chunk_id"] == 101
        assert cites[0]["chunk_index"] == 3
        assert cites[0]["page"] == 2
        assert cites[0]["bbox_list"] == [[1, 2, 3, 4]]
        assert cites[0]["document_id"] == 7
        assert cites[0]["document_title"] == "Paper A"

    def test_lowercase_tolerated(self):
        assert parse_citations("見 [c102]", CHUNKS)[0]["chunk_id"] == 102

    def test_stale_index_style_labels_ignored(self):
        # 舊協定的 chunk_index 標籤（3/7）不在 id 對照表 → 忽略，不錯配
        assert parse_citations("舊標籤 [C3][C7]", CHUNKS) == []

    def test_no_citations(self):
        assert parse_citations("文獻中未提及。", CHUNKS) == []


class TestBuildMessages:
    def test_document_scope_context(self):
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        msgs = build_messages(
            CHUNKS[:1],
            history,
            "問題",
            scope="document",
            scope_title="Test Paper",
            selection_text="選取文字",
        )
        assert msgs[0]["role"] == "system"
        assert "# 文獻：Test Paper" in msgs[0]["content"]
        assert "[C101] (p.2) 方法段" in msgs[0]["content"]
        assert "《" not in msgs[0]["content"].split("# 可引用段落")[1]
        assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
        assert "選取文字" in msgs[-1]["content"]

    def test_project_scope_lists_sources(self):
        msgs = build_messages(CHUNKS, [], "q", scope="project", scope_title="我的專案")
        system = msgs[0]["content"]
        assert "# 專案：我的專案" in system
        assert "《Paper A》" in system and "《Paper B》" in system
        assert "[C101]（《Paper A》 p.2）方法段" in system

    def test_library_scope_header(self):
        msgs = build_messages(CHUNKS, [], "q", scope="library")
        assert "# 範圍：全部文獻" in msgs[0]["content"]

    def test_history_truncated_and_empty_filtered(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
        history.insert(5, {"role": "assistant", "content": "   "})
        msgs = build_messages([], history, "q", scope="library")
        assert len(msgs) == 1 + 10 + 1


class TestRetrieveContextDispatch:
    """驗證 scope → repo 參數的分派（SQL 隔離本體由 eval_citations --scope 驗證）。"""

    @pytest.fixture
    def spy(self, monkeypatch):
        calls = {}

        async def fake_scoped(session, embedding, k, *, doc_id=None, project_id=None):
            calls.update({"k": k, "doc_id": doc_id, "project_id": project_id})
            return []

        monkeypatch.setattr(rag.repo, "similar_chunks_scoped", fake_scoped)
        return calls

    async def test_document_scope(self, spy):
        await rag.retrieve_context(None, [0.1], scope="document", doc_id=5)
        assert spy == {"k": rag.TOP_K_DOCUMENT, "doc_id": 5, "project_id": None}

    async def test_project_scope(self, spy):
        await rag.retrieve_context(None, [0.1], scope="project", project_id=9)
        assert spy == {"k": rag.TOP_K_MULTI, "doc_id": None, "project_id": 9}

    async def test_library_scope(self, spy):
        await rag.retrieve_context(None, [0.1], scope="library")
        assert spy == {"k": rag.TOP_K_MULTI, "doc_id": None, "project_id": None}


class TestExtractJson:
    def test_with_fence_and_noise(self):
        text = '前言```json\n{"a": {"b": 1}}\n```後記'
        assert extract_json(text) == {"a": {"b": 1}}
