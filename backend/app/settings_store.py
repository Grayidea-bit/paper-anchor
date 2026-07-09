"""執行期使用者設定（DB 持久化 + 記憶體快取）。

.env 是預設值；settings 表的值可在執行期覆蓋（LLM 來源、系統提示詞、自訂工具）。
llm.py / rag.py 以同步的 runtime() 讀快取，避免在串流熱路徑上查 DB。
"""

import json
import logging

from sqlalchemy import text

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 允許的設定鍵（白名單，防止任意寫入）；工具是 code-based（app/tools/），不走設定
ALLOWED_KEYS = {
    "llm_base_url",
    "llm_api_key",
    "llm_chat_model",
    "system_prompt_extra",
    # M8：Claude Agent SDK 後端（訂閱額度，官方 setup-token 貼碼）
    "chat_backend",  # "openai" | "claude-sdk"
    "claude_oauth_token",  # CLAUDE_CODE_OAUTH_TOKEN（setup-token 產出的一年效期 token）
    "claude_model",  # 別名 "sonnet"/"opus"/"haiku" 或完整 id
    # M9：openai/NIM 來源的可選模型清單（對話區下拉；JSON 陣列）
    "llm_chat_models",
    # T-TR-01：翻譯表目標語言（顯示用字串，直接進 prompt；缺省回落「繁體中文」）
    "translation_target_lang",
    # M12：單向備份到 Google Drive（D10）
    "gdrive_client_id",  # 使用者 Desktop app OAuth client id
    "gdrive_client_secret",  # OAuth client secret（SECRET_KEYS 遮罩）
    "gdrive_refresh_token",  # OAuth refresh token（callback 取得後寫入；PUT 不開放）
    "backup_interval_hours",  # 定時備份間隔小時數（0＝關閉）
    "backup_last_run",  # 上次備份時間與結果摘要（服務層寫入；PUT 不開放）
    # M13：從 Drive 匯入還原（D11）
    "restore_last_run",  # 上次還原時間與結果摘要（服務層寫入；PUT 不開放）
    # M14：本地 embedding 來源選擇（非 secret；embed 端點/key/模型仍走 .env，見 D12）
    "embed_source",  # "auto" | "nim" | "local"
}
SECRET_KEYS = {
    "llm_api_key",
    "claude_oauth_token",
    "gdrive_client_secret",
    "gdrive_refresh_token",
}

_cache: dict | None = None


async def ensure_loaded() -> dict:
    global _cache
    if _cache is None:
        async with SessionLocal() as session:
            rows = await session.execute(text("SELECT key, value FROM settings"))
            _cache = {r.key: r.value for r in rows}
        logger.info("settings loaded: %s keys", len(_cache))
    return _cache


def runtime(key: str, default=None):
    """同步讀快取（未載入時回 default；lifespan 啟動時已載入）。"""
    if _cache is None:
        return default
    return _cache.get(key, default)


async def update(values: dict) -> dict:
    """白名單合併寫入；空字串視為清除該鍵（回落 .env 預設）。"""
    global _cache
    await ensure_loaded()
    async with SessionLocal() as session:
        for key, value in values.items():
            if key not in ALLOWED_KEYS:
                continue
            if value in ("", None):
                await session.execute(text("DELETE FROM settings WHERE key = :key"), {"key": key})
                _cache.pop(key, None)
            else:
                await session.execute(
                    text(
                        """
                        INSERT INTO settings (key, value) VALUES (:key, CAST(:value AS jsonb))
                        ON CONFLICT (key)
                        DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                        """
                    ),
                    {"key": key, "value": json.dumps(value)},
                )
                _cache[key] = value
        await session.commit()
    return dict(_cache)


def masked_view() -> dict:
    """對外呈現：秘密鍵只回「已設定」布林。"""
    data = dict(_cache or {})
    out = {k: v for k, v in data.items() if k not in SECRET_KEYS}
    for key in SECRET_KEYS:
        out[f"{key}_set"] = bool(data.get(key))
    return out
