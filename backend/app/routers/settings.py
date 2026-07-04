from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import settings_store
from app.config import get_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    """欄位缺席＝不變；空字串＝清除（回落 .env 預設）。"""

    llm_base_url: str | None = Field(default=None, max_length=300)
    llm_api_key: str | None = Field(default=None, max_length=300)
    llm_chat_model: str | None = Field(default=None, max_length=200)
    system_prompt_extra: str | None = Field(default=None, max_length=4000)


def _view() -> dict:
    env = get_settings()
    data = settings_store.masked_view()
    # 附上 .env 預設值供 UI 顯示 placeholder
    data["defaults"] = {
        "llm_base_url": env.llm_base_url,
        "llm_chat_model": env.llm_chat_model,
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
