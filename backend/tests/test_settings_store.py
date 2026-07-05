from app import settings_store
from app.routers.settings import SettingsUpdate


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

    def test_claude_keys_allowed(self):
        # M8：Claude Agent SDK 後端（訂閱額度，setup-token 貼碼）新鍵
        assert "chat_backend" in settings_store.ALLOWED_KEYS
        assert "claude_oauth_token" in settings_store.ALLOWED_KEYS
        assert "claude_model" in settings_store.ALLOWED_KEYS

    def test_llm_chat_models_allowed(self):
        # M9：openai/NIM 來源可選模型清單（對話區下拉）
        assert "llm_chat_models" in settings_store.ALLOWED_KEYS


class TestRouterModelCoverage:
    """守護 router 請求模型與 store 白名單的一致性。

    T-TR-01 曾只加白名單漏加 SettingsUpdate 欄位：Pydantic 靜默丟棄未知欄位，
    PUT 回 200 但什麼都沒存（GET 不含該鍵、UI 還原、翻譯永遠用預設）。
    """

    # claude_model：M9 改為每對話選模型後僅存唯讀回落（claude_backend.py），無寫入端點
    WRITE_EXEMPT = {"claude_model"}

    def test_translation_target_lang_updatable(self):
        assert "translation_target_lang" in SettingsUpdate.model_fields

    def test_every_allowed_key_has_router_field(self):
        missing = settings_store.ALLOWED_KEYS - self.WRITE_EXEMPT - set(SettingsUpdate.model_fields)
        assert not missing, f"白名單鍵缺 SettingsUpdate 欄位（PUT 會被靜默丟棄）: {missing}"

    def test_update_payload_passthrough(self):
        body = SettingsUpdate(translation_target_lang="English")
        assert body.model_dump(exclude_none=True) == {"translation_target_lang": "English"}


class TestClaudeSecretKeys:
    def test_claude_token_is_secret(self):
        assert "claude_oauth_token" in settings_store.SECRET_KEYS

    def test_masked_view_only_exposes_set_booleans(self, monkeypatch):
        monkeypatch.setattr(
            settings_store,
            "_cache",
            {
                "claude_oauth_token": "secret-access-token",
                "claude_model": "sonnet",
                "chat_backend": "claude-sdk",
            },
        )
        view = settings_store.masked_view()
        assert "claude_oauth_token" not in view
        assert view["claude_oauth_token_set"] is True
        # 非秘密鍵照常回顯
        assert view["claude_model"] == "sonnet"
        assert view["chat_backend"] == "claude-sdk"

    def test_masked_view_false_when_absent(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})
        view = settings_store.masked_view()
        assert view["claude_oauth_token_set"] is False
