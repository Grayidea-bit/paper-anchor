from app import tools
from app.db.repo import escape_like


class TestDiscovery:
    def setup_method(self):
        tools.reset_cache()

    def test_keyword_search_registered(self):
        names = [t["name"] for t in tools.list_tools()]
        assert "keyword_search" in names

    def test_template_not_registered(self):
        names = [t["name"] for t in tools.list_tools()]
        assert "my_tool" not in names  # template_tool.py ENABLED=False

    def test_list_tools_shape(self):
        for t in tools.list_tools():
            assert t["name"] and isinstance(t["description"], str)

    def test_build_toolset_none_when_all_disabled(self, monkeypatch):
        monkeypatch.setattr(tools, "_discovered", [])
        assert tools.build_toolset() is None

    def test_build_toolset_present(self):
        assert tools.build_toolset() is not None


class TestEscapeLike:
    def test_wildcards_escaped(self):
        assert escape_like("100%_done") == "100\\%\\_done"

    def test_backslash_escaped_first(self):
        assert escape_like("a\\%b") == "a\\\\\\%b"

    def test_plain_text_unchanged(self):
        assert escape_like("KSDD2") == "KSDD2"
