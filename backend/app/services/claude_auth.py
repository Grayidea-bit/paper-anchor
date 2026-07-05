"""Claude 訂閱 token 取用（官方 `claude setup-token` 貼碼流程）。

登入方式：使用者在本機執行 `claude setup-token`（需 Pro/Max/Team/Enterprise 訂閱），
把產出的一年效期 token 貼進設定頁 → 存 settings_store（SECRET_KEYS，永不回顯）。

不內建 OAuth authorize/token 端點：那些端點未官方公開、屬逆向且不受支援，
放進開源專案有相容性與授權風險（見 docs/03-roadmap.md M8）。setup-token 是
Anthropic 唯一背書的無互動式登入方式。

安全：容器絕不設 ANTHROPIC_API_KEY（auth 優先序會蓋過 OAuth token）。
"""

from app import settings_store
from app.llm import LLMError


async def ensure_token() -> str:
    """claude_backend 呼叫前取用有效 token。缺 token → LLMError 提示去設定頁。"""
    token = settings_store.runtime("claude_oauth_token")
    if not token:
        raise LLMError(
            "Claude 訂閱未登入：請在本機執行 `claude setup-token`，將產出的 token 貼進設定頁"
        )
    return token


async def logout() -> None:
    """登出：清除已存 token（空字串＝刪除，回落未登入）。"""
    await settings_store.update({"claude_oauth_token": ""})
