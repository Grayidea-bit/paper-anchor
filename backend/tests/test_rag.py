from app.llm import ThinkFilter, extract_json
from app.services.rag import build_messages, parse_citations

CHUNKS = [
    {"id": 101, "chunk_index": 3, "page": 2, "content": "方法段", "bbox_list": [[1, 2, 3, 4]]},
    {"id": 102, "chunk_index": 7, "page": 5, "content": "實驗段", "bbox_list": [[5, 6, 7, 8]]},
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
    def test_extracts_known_indexes_in_order(self):
        answer = "方法如上 [C3]，結果見 [C7][C3]，虛構 [C99] 忽略。"
        cites = parse_citations(answer, CHUNKS)
        assert [c["chunk_index"] for c in cites] == [3, 7]
        assert cites[0]["page"] == 2
        assert cites[0]["bbox_list"] == [[1, 2, 3, 4]]

    def test_lowercase_tolerated(self):
        assert parse_citations("見 [c7]", CHUNKS)[0]["chunk_index"] == 7

    def test_no_citations(self):
        assert parse_citations("文獻中未提及。", CHUNKS) == []


class TestBuildMessages:
    def test_context_and_history_assembled(self):
        doc = {"title": "Test Paper"}
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        msgs = build_messages(doc, CHUNKS, history, "問題", selection_text="選取文字")
        assert msgs[0]["role"] == "system"
        assert "[C3] (p.2) 方法段" in msgs[0]["content"]
        assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
        assert "選取文字" in msgs[-1]["content"]

    def test_history_truncated_to_limit(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
        msgs = build_messages({"title": "t"}, [], history, "q")
        assert len(msgs) == 1 + 10 + 1


class TestExtractJson:
    def test_with_fence_and_noise(self):
        text = '前言```json\n{"a": {"b": 1}}\n```後記'
        assert extract_json(text) == {"a": {"b": 1}}
