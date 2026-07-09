"""T-FD-07：Claude 後端 _input_schema/_tool_description 對齊 Pydantic AI 側品質。

涵蓋：
- keyword_search（真實工具）的 schema 含逐參數描述、required 正確、型別正確。
- 型別映射矩陣（list[str] / dict / Optional / 無預設值的 Optional / 無法映射型別 /
  缺型別註記）。
- 與 Pydantic AI 側對照的煙囪斷言：required 參數集合一致（不逐字比 description）。
"""

from app import tools as tools_pkg
from app.tools.keyword_search import keyword_search


class _Unmappable:
    """故意無法映射到任何 JSON Schema 型別的自訂類別。"""


async def _fake_tool(
    ctx,
    required_str: str,
    items: list[str],
    meta: dict,
    *,
    limit: int = 3,
    threshold: float = 0.5,
    flag: bool = False,
    note: str | None = None,
    alt: int | None,
    bare_list: list,
    weird: _Unmappable,
    mystery,
) -> str:
    """假工具，涵蓋型別映射矩陣（不會被註冊，只用來測 _input_schema）。

    Args:
        required_str: 必填字串，無預設值。
        items: 字串陣列。
        meta: 任意物件（dict）。
        limit: 有預設值，選填（int）。
        threshold: 有預設值，選填（float）。
        flag: 有預設值，選填（bool）。
        note: 明確 Optional，即使沒有預設值也視為 optional。
        alt: Optional 但沒有預設值，一樣要視為 optional。
        bare_list: 沒有 subscript 的 list。
        weird: 無法映射的自訂型別，應退回 string 並記警告。
        mystery: 缺型別註記，應退回 string 並記警告。
    """
    return "ok"


class TestInputSchemaKeywordSearch:
    def test_properties_have_descriptions(self):
        schema = tools_pkg._input_schema(keyword_search)
        assert schema["type"] == "object"
        props = schema["properties"]
        assert set(props) == {"query", "max_results"}
        assert props["query"]["description"]
        assert props["max_results"]["description"]

    def test_types_mapped_correctly(self):
        props = tools_pkg._input_schema(keyword_search)["properties"]
        assert props["query"]["type"] == "string"
        assert props["max_results"]["type"] == "integer"

    def test_required_excludes_defaulted_param(self):
        schema = tools_pkg._input_schema(keyword_search)
        assert schema["required"] == ["query"]

    def test_default_value_noted_in_description(self):
        props = tools_pkg._input_schema(keyword_search)["properties"]
        # max_results 有預設值 5，描述應附註預設值
        assert "5" in props["max_results"]["description"]

    def test_tool_description_excludes_args_section(self):
        desc = tools_pkg._tool_description(keyword_search)
        assert desc  # 非空
        assert "Args" not in desc
        assert "query:" not in desc
        assert "max_results:" not in desc


class TestInputSchemaTypeMappingMatrix:
    def setup_method(self):
        self.schema = tools_pkg._input_schema(_fake_tool)
        self.props = self.schema["properties"]
        self.required = set(self.schema["required"])

    def test_ctx_stripped(self):
        assert "ctx" not in self.props

    def test_str(self):
        assert self.props["required_str"]["type"] == "string"
        assert "required_str" in self.required

    def test_list_of_str(self):
        assert self.props["items"] == {
            "type": "array",
            "items": {"type": "string"},
            "description": "字串陣列。",
        }
        assert "items" in self.required

    def test_dict(self):
        assert self.props["meta"]["type"] == "object"
        assert "meta" in self.required

    def test_defaulted_int_is_optional_and_noted(self):
        assert self.props["limit"]["type"] == "integer"
        assert "limit" not in self.required
        assert "3" in self.props["limit"]["description"]

    def test_defaulted_float_is_optional(self):
        assert self.props["threshold"]["type"] == "number"
        assert "threshold" not in self.required

    def test_defaulted_bool_is_optional(self):
        assert self.props["flag"]["type"] == "boolean"
        assert "flag" not in self.required

    def test_optional_with_default_none(self):
        assert self.props["note"]["type"] == "string"
        assert "note" not in self.required

    def test_optional_without_default_is_still_optional(self):
        # alt: int | None 沒有預設值，仍應視為 optional（剝殼取 int）。
        assert self.props["alt"]["type"] == "integer"
        assert "alt" not in self.required

    def test_bare_list_defaults_items_to_string(self):
        assert self.props["bare_list"]["type"] == "array"
        assert self.props["bare_list"]["items"] == {"type": "string"}
        assert "bare_list" in self.required

    def test_unmappable_type_falls_back_to_string_and_warns(self, caplog):
        with caplog.at_level("WARNING", logger="app.tools"):
            schema = tools_pkg._input_schema(_fake_tool)
        assert schema["properties"]["weird"]["type"] == "string"
        assert "weird" in schema["required"]
        assert any("weird" in r.message for r in caplog.records)

    def test_missing_annotation_falls_back_to_string_and_warns(self, caplog):
        with caplog.at_level("WARNING", logger="app.tools"):
            schema = tools_pkg._input_schema(_fake_tool)
        assert schema["properties"]["mystery"]["type"] == "string"
        assert "mystery" in schema["required"]
        assert any("mystery" in r.message for r in caplog.records)


class TestConsistencyWithPydanticAiSide:
    """煙囪斷言：Claude 側與 Pydantic AI 側（OpenAI 後端）required 集合一致。"""

    def test_required_set_matches(self):
        from pydantic_ai._function_schema import function_schema
        from pydantic_ai.tools import GenerateToolJsonSchema

        pyd_schema = function_schema(
            keyword_search, GenerateToolJsonSchema, docstring_format="google"
        )
        pyd_required = set(pyd_schema.json_schema.get("required", []))

        claude_schema = tools_pkg._input_schema(keyword_search)
        assert set(claude_schema["required"]) == pyd_required
