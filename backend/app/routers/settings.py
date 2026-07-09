from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints

from app import settings_store
from app.config import get_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

_ModelId = Annotated[str, StringConstraints(max_length=200)]


class SettingsUpdate(BaseModel):
    """欄位缺席＝不變；空字串＝清除（回落 .env 預設）。"""

    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_api_key: str | None = Field(default=None, max_length=300)
    llm_chat_model: str | None = Field(default=None, max_length=200)
    # M9：openai/NIM 來源可選模型清單（對話區下拉；設定頁只維護清單本身）
    llm_chat_models: list[_ModelId] | None = Field(default=None)
    system_prompt_extra: str | None = Field(default=None, max_length=4000)
    # M8：Claude Agent SDK 後端
    chat_backend: str | None = Field(default=None, pattern=r"^(openai|claude-sdk)$")
    # 進階退路：直接貼 `claude setup-token` 產出的長效 token
    claude_oauth_token: str | None = Field(default=None, max_length=2000)
    # T-TR-01：翻譯表目標語言（任意字串直接進 prompt；空字串＝清除回落預設）
    translation_target_lang: str | None = Field(default=None, max_length=60)
    # M12：Google Drive 備份 OAuth 設定（gdrive_refresh_token / backup_last_run
    # 不開放 PUT——由 callback／服務層寫入，見 test_settings_store WRITE_EXEMPT）
    gdrive_client_id: str | None = Field(default=None, max_length=300)
    gdrive_client_secret: str | None = Field(default=None, max_length=300)
    backup_interval_hours: int | None = Field(default=None, ge=0, le=8760)
    # M14：本地 embedding 來源選擇（僅來源執行期化，見 D12）
    embed_source: str | None = Field(default=None, pattern=r"^(auto|nim|local)$")


def _view() -> dict:
    env = get_settings()
    data = settings_store.masked_view()
    # 附上 .env 預設值供 UI 顯示 placeholder
    data["defaults"] = {
        "llm_base_url": env.llm_base_url,
        "llm_chat_model": env.llm_chat_model,
        "llm_chat_models": [env.llm_chat_model],
        "chat_backend": "openai",
    }
    return data


@router.get("")
async def get_user_settings() -> dict:
    await settings_store.ensure_loaded()
    return _view()


@router.put("")
async def update_user_settings(body: SettingsUpdate) -> dict:
    await settings_store.update(body.model_dump(exclude_none=True))
    return _view()
