"""M14 本地 embedding + llm 分派測試（T-EM-01，見 docs/02-architecture.md D12）。

不真下載模型：`local_embed` 的 fastembed 呼叫一律 mock（`_get_model` 直接替換掉，
或 monkeypatch `sys.modules["fastembed"]`）；`fastembed` 在主機測試環境未安裝，
`local_embed.py` 只在函式內 import 它，故本檔全程不會真的 `import fastembed`。
"""

import sys
import types

import pytest

from app import llm, local_embed, settings_store
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(settings_store, "_cache", {})
    local_embed.reset_for_tests()
    yield
    local_embed.reset_for_tests()


# ---------- effective_embed_config 矩陣 ----------


class TestEffectiveEmbedConfig:
    def test_auto_with_key_uses_nim(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "auto"})
        env = get_settings()
        monkeypatch.setattr(env, "embed_api_key", "nvapi-xxx")
        source, model, dim = llm.effective_embed_config()
        assert (source, model, dim) == ("nim", env.embed_model, env.embed_dim)

    def test_auto_without_key_uses_local(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "auto"})
        env = get_settings()
        monkeypatch.setattr(env, "embed_api_key", "")
        source, model, dim = llm.effective_embed_config()
        assert source == "local"
        assert model == local_embed.LOCAL_EMBED_MODEL
        assert dim == local_embed.LOCAL_EMBED_DIM

    def test_default_source_is_auto(self, monkeypatch):
        # 未設定 embed_source 鍵時，行為等同 auto
        monkeypatch.setattr(settings_store, "_cache", {})
        env = get_settings()
        monkeypatch.setattr(env, "embed_api_key", "")
        source, _, _ = llm.effective_embed_config()
        assert source == "local"

    def test_nim_forced_with_key(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "nim"})
        env = get_settings()
        monkeypatch.setattr(env, "embed_api_key", "nvapi-xxx")
        source, model, dim = llm.effective_embed_config()
        assert (source, model, dim) == ("nim", env.embed_model, env.embed_dim)

    def test_nim_forced_without_key_raises(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "nim"})
        env = get_settings()
        monkeypatch.setattr(env, "embed_api_key", "")
        with pytest.raises(llm.LLMError):
            llm.effective_embed_config()

    def test_local_forced_ignores_nim_key(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "local"})
        env = get_settings()
        monkeypatch.setattr(env, "embed_api_key", "nvapi-xxx")
        source, model, dim = llm.effective_embed_config()
        assert source == "local"
        assert model == local_embed.LOCAL_EMBED_MODEL
        assert dim == local_embed.LOCAL_EMBED_DIM


# ---------- embed_passages / embed_query 分派 ----------


class TestDispatch:
    async def test_embed_passages_routes_to_nim(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "nim"})
        monkeypatch.setattr(get_settings(), "embed_api_key", "nvapi-xxx")

        called = {}

        async def fake_embed(texts, input_type):
            called["texts"], called["input_type"] = texts, input_type
            return [[0.1] for _ in texts]

        async def fail_local(texts, input_type):
            raise AssertionError("不應呼叫本地路徑")

        monkeypatch.setattr(llm, "_embed", fake_embed)
        monkeypatch.setattr(local_embed, "embed_local", fail_local)

        result = await llm.embed_passages(["a", "b"])
        assert result == [[0.1], [0.1]]
        assert called == {"texts": ["a", "b"], "input_type": "passage"}

    async def test_embed_query_routes_to_nim(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "nim"})
        monkeypatch.setattr(get_settings(), "embed_api_key", "nvapi-xxx")

        async def fake_embed(texts, input_type):
            assert input_type == "query"
            return [[0.9] for _ in texts]

        async def fail_local(texts, input_type):
            raise AssertionError("不應呼叫本地路徑")

        monkeypatch.setattr(llm, "_embed", fake_embed)
        monkeypatch.setattr(local_embed, "embed_local", fail_local)

        result = await llm.embed_query("q")
        assert result == [0.9]

    async def test_embed_passages_routes_to_local(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "local"})

        called = {}

        async def fake_local(texts, input_type):
            called["texts"], called["input_type"] = texts, input_type
            return [[0.2] for _ in texts]

        async def fail_nim(texts, input_type):
            raise AssertionError("不應呼叫 NIM 路徑")

        monkeypatch.setattr(local_embed, "embed_local", fake_local)
        monkeypatch.setattr(llm, "_embed", fail_nim)

        result = await llm.embed_passages(["a", "b", "c"])
        assert result == [[0.2], [0.2], [0.2]]
        assert called == {"texts": ["a", "b", "c"], "input_type": "passage"}

    async def test_embed_query_routes_to_local(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"embed_source": "local"})

        async def fake_local(texts, input_type):
            assert input_type == "query"
            return [[0.3] for _ in texts]

        async def fail_nim(texts, input_type):
            raise AssertionError("不應呼叫 NIM 路徑")

        monkeypatch.setattr(local_embed, "embed_local", fake_local)
        monkeypatch.setattr(llm, "_embed", fail_nim)

        result = await llm.embed_query("q")
        assert result == [0.3]

    async def test_auto_without_key_dispatches_local(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})
        monkeypatch.setattr(get_settings(), "embed_api_key", "")

        async def fake_local(texts, input_type):
            return [[0.5] for _ in texts]

        async def fail_nim(texts, input_type):
            raise AssertionError("不應呼叫 NIM 路徑")

        monkeypatch.setattr(local_embed, "embed_local", fake_local)
        monkeypatch.setattr(llm, "_embed", fail_nim)

        result = await llm.embed_passages(["x"])
        assert result == [[0.5]]


# ---------- local_embed 模組（fastembed 全 mock，不真下載） ----------


class _FakeTextEmbedding:
    """模擬 fastembed.TextEmbedding：記錄呼叫次數與參數，回傳固定維度向量。"""

    add_custom_model_calls: list[dict] = []
    init_calls: list[dict] = []
    embed_calls: list[dict] = []
    dim = local_embed.LOCAL_EMBED_DIM

    def __init__(self, model_name, cache_dir, threads):
        _FakeTextEmbedding.init_calls.append(
            {"model_name": model_name, "cache_dir": cache_dir, "threads": threads}
        )

    @classmethod
    def add_custom_model(cls, **kwargs):
        cls.add_custom_model_calls.append(kwargs)

    def embed(self, texts, batch_size):
        _FakeTextEmbedding.embed_calls.append({"texts": texts, "batch_size": batch_size})
        return [[0.1] * self.dim for _ in texts]


def _install_fake_fastembed(monkeypatch, text_embedding_cls=_FakeTextEmbedding):
    """把 sys.modules 塞入假的 fastembed / fastembed.common.model_description，
    讓 local_embed.py 內的函式內 `from fastembed import ...` 命中假模組。
    """
    fastembed_mod = types.ModuleType("fastembed")
    fastembed_mod.TextEmbedding = text_embedding_cls

    common_mod = types.ModuleType("fastembed.common")
    model_desc_mod = types.ModuleType("fastembed.common.model_description")

    class PoolingType:
        CLS = "cls"

    class ModelSource:
        def __init__(self, hf):
            self.hf = hf

    model_desc_mod.PoolingType = PoolingType
    model_desc_mod.ModelSource = ModelSource

    monkeypatch.setitem(sys.modules, "fastembed", fastembed_mod)
    monkeypatch.setitem(sys.modules, "fastembed.common", common_mod)
    monkeypatch.setitem(sys.modules, "fastembed.common.model_description", model_desc_mod)


class TestLocalEmbedLazyLoad:
    def test_get_model_registers_and_instantiates_once(self, monkeypatch):
        _FakeTextEmbedding.add_custom_model_calls = []
        _FakeTextEmbedding.init_calls = []
        _install_fake_fastembed(monkeypatch)

        m1 = local_embed._get_model()
        m2 = local_embed._get_model()

        assert m1 is m2  # 單例：第二次呼叫不重新初始化
        assert len(_FakeTextEmbedding.add_custom_model_calls) == 1
        assert len(_FakeTextEmbedding.init_calls) == 1
        call = _FakeTextEmbedding.add_custom_model_calls[0]
        assert call["model"] == local_embed.LOCAL_EMBED_MODEL
        assert call["dim"] == local_embed.LOCAL_EMBED_DIM
        assert call["model_file"] == "onnx/model.onnx"
        assert call["additional_files"] == ["onnx/model.onnx_data"]
        init_call = _FakeTextEmbedding.init_calls[0]
        assert init_call["threads"] == 8

    def test_load_failure_raises_llm_error(self, monkeypatch):
        class _BoomTextEmbedding:
            @classmethod
            def add_custom_model(cls, **kwargs):
                raise RuntimeError("network unreachable")

        _install_fake_fastembed(monkeypatch, text_embedding_cls=_BoomTextEmbedding)

        with pytest.raises(llm.LLMError):
            local_embed._get_model()

    def test_reset_for_tests_forces_reload(self, monkeypatch):
        _FakeTextEmbedding.init_calls = []
        _install_fake_fastembed(monkeypatch)

        local_embed._get_model()
        local_embed.reset_for_tests()
        local_embed._get_model()

        assert len(_FakeTextEmbedding.init_calls) == 2


class TestEmbedLocal:
    async def test_embed_local_uses_to_thread_and_batches(self, monkeypatch):
        _FakeTextEmbedding.embed_calls = []
        _install_fake_fastembed(monkeypatch)

        result = await local_embed.embed_local(["a", "b"], "passage")

        assert len(result) == 2
        assert all(len(vec) == local_embed.LOCAL_EMBED_DIM for vec in result)
        assert _FakeTextEmbedding.embed_calls[0]["batch_size"] == 4

    async def test_embed_local_passage_and_query_same_path(self, monkeypatch):
        # BGE-M3 對稱模型，免前綴：驗證 texts 原封不動傳給底層 model.embed
        _FakeTextEmbedding.embed_calls = []
        _install_fake_fastembed(monkeypatch)

        await local_embed.embed_local(["hello"], "query")
        await local_embed.embed_local(["hello"], "passage")

        assert _FakeTextEmbedding.embed_calls[0]["texts"] == ["hello"]
        assert _FakeTextEmbedding.embed_calls[1]["texts"] == ["hello"]

    async def test_embed_local_bad_dim_raises(self, monkeypatch):
        class _BadDimTextEmbedding(_FakeTextEmbedding):
            def embed(self, texts, batch_size):
                return [[0.1, 0.2] for _ in texts]  # 錯誤維度

        _install_fake_fastembed(monkeypatch, text_embedding_cls=_BadDimTextEmbedding)

        with pytest.raises(llm.LLMError):
            await local_embed.embed_local(["x"], "passage")
