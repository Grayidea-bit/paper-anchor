from app import settings_store


class TestRuntime:
    def test_default_when_not_loaded(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", None)
        assert settings_store.runtime("llm_base_url", "fallback") == "fallback"

    def test_reads_cache(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"llm_chat_model": "m1"})
        assert settings_store.runtime("llm_chat_model") == "m1"
        assert settings_store.runtime("missing", "d") == "d"


class TestMaskedView:
    def test_secret_masked(self, monkeypatch):
        monkeypatch.setattr(
            settings_store,
            "_cache",
            {"llm_api_key": "nvapi-secret", "llm_chat_model": "m1"},
        )
        view = settings_store.masked_view()
        assert "llm_api_key" not in view
        assert view["llm_api_key_set"] is True
        assert view["llm_chat_model"] == "m1"

    def test_secret_absent(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})
        assert settings_store.masked_view()["llm_api_key_set"] is False


class TestAllowedKeys:
    def test_whitelist_contents(self):
        assert "llm_api_key" in settings_store.ALLOWED_KEYS
        assert "custom_tools" not in settings_store.ALLOWED_KEYS
