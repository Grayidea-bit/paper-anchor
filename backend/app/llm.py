"""唯一的 LLM 供應商出入口（CLAUDE.md 鐵律 3）。

NIM 注意事項（docs/02-architecture.md D5）：
- embedding 必帶 input_type：入庫 "passage"、查詢 "query"，用錯不報錯但檢索品質劣化。
- 單筆長度受模型 512 token 上限約束，帶 truncate:"END" 保險；批量分批送出。
- chat 預設模型是推理模型：reasoning_content 直接丟棄，content 內的 <think> 段以
  ThinkFilter 過濾，只輸出最終答案。
"""

import asyncio
import json
import re
import time
from collections import deque

import httpx

from app import local_embed, settings_store
from app.config import get_settings

EMBED_BATCH_SIZE = 32
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_MAX_ATTEMPTS = 3
_RETRY_MARKERS = ("resourceexhausted", "rate limit", "429", "overloaded", "503")


class LLMError(RuntimeError):
    pass


# ---------- 執行期覆蓋（設定頁）與 RPM ----------


def _default_chat_model() -> str:
    """openai 來源預設 model：llm_chat_models 第一項 > llm_chat_model > env 預設。"""
    env = get_settings()
    models = settings_store.runtime("llm_chat_models")
    if isinstance(models, list) and models:
        return models[0]
    return settings_store.runtime("llm_chat_model") or env.llm_chat_model


def _chat_config() -> tuple[str, str, str]:
    """chat 的 (base_url, api_key, model)：settings 覆蓋 > .env。"""
    env = get_settings()
    return (
        settings_store.runtime("llm_base_url") or env.llm_base_url,
        settings_store.runtime("llm_api_key") or env.llm_api_key,
        _default_chat_model(),
    )


_request_times: deque[float] = deque(maxlen=512)


def _record_request() -> None:
    _request_times.append(time.monotonic())


def current_rpm() -> int:
    """最近 60 秒內的 LLM API 請求數（chat + embedding）。"""
    cutoff = time.monotonic() - 60
    return sum(1 for ts in _request_times if ts >= cutoff)


def _is_retryable(message: str) -> bool:
    low = message.lower()
    return any(m in low for m in _RETRY_MARKERS)


async def _backoff(attempt: int) -> None:
    await asyncio.sleep(4 * (attempt + 1))


# ---------- embeddings ----------


async def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    settings = get_settings()
    results: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            for attempt in range(_MAX_ATTEMPTS):
                _record_request()
                resp = await client.post(
                    f"{settings.embed_base_url}/embeddings",
                    headers={"Authorization": f"Bearer {settings.embed_api_key}"},
                    json={
                        "model": settings.embed_model,
                        "input": batch,
                        "input_type": input_type,
                        "truncate": "END",
                    },
                )
                if resp.status_code == 200:
                    break
                if attempt < _MAX_ATTEMPTS - 1 and _is_retryable(resp.text):
                    await _backoff(attempt)
                    continue
                raise LLMError(f"embedding API {resp.status_code}: {resp.text[:300]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            results.extend(d["embedding"] for d in data)
    return results


def effective_embed_config() -> tuple[str, str, int]:
    """embedding 來源單一真相（docs/02-architecture.md D12）。

    回傳 `(source, model, dim)`，`source` 為 `"nim"` 或 `"local"`——反映實際生效
    來源，而非 `.env` 原值。backup manifest、restore 模型相符判斷、healthz、前端
    顯示一律共用本函式（不得各自重算）。

    `embed_source` 設定三值：
    - `"nim"`：強制 NIM（無 key 則明確報錯，不默默改用本地污染向量）。
    - `"local"`：強制本地模型，忽略 NIM 設定。
    - `"auto"`（預設）：有 `embed_api_key` 就用 NIM，否則落本地。
    """
    env = get_settings()
    source = settings_store.runtime("embed_source", "auto")
    if source == "nim":
        if not env.embed_api_key:
            raise LLMError("embed_source=nim 但未設定 embed_api_key")
        return "nim", env.embed_model, env.embed_dim
    if source == "local":
        return "local", local_embed.LOCAL_EMBED_MODEL, local_embed.LOCAL_EMBED_DIM
    # auto
    if env.embed_api_key:
        return "nim", env.embed_model, env.embed_dim
    return "local", local_embed.LOCAL_EMBED_MODEL, local_embed.LOCAL_EMBED_DIM


async def embed_passages(texts: list[str]) -> list[list[float]]:
    source, _, _ = effective_embed_config()
    if source == "local":
        return await local_embed.embed_local(texts, "passage")
    return await _embed(texts, "passage")


async def embed_query(text: str) -> list[float]:
    source, _, _ = effective_embed_config()
    if source == "local":
        return (await local_embed.embed_local([text], "query"))[0]
    return (await _embed([text], "query"))[0]


# ---------- chat ----------


class ThinkFilter:
    """串流中過濾 <think>…</think>（標籤可能被切在不同 chunk）。"""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._inside = False
        self._buf = ""

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while True:
            if self._inside:
                pos = self._buf.find(self.CLOSE)
                if pos < 0:
                    self._buf = self._buf[-(len(self.CLOSE) - 1) :]
                    break
                self._buf = self._buf[pos + len(self.CLOSE) :]
                self._inside = False
            else:
                pos = self._buf.find(self.OPEN)
                if pos >= 0:
                    out.append(self._buf[:pos])
                    self._buf = self._buf[pos + len(self.OPEN) :]
                    self._inside = True
                    continue
                # 保留可能是 <think 前綴的尾巴，其餘輸出
                keep = 0
                for k in range(min(len(self.OPEN) - 1, len(self._buf)), 0, -1):
                    if self._buf.endswith(self.OPEN[:k]):
                        keep = k
                        break
                if keep:
                    out.append(self._buf[:-keep])
                    self._buf = self._buf[-keep:]
                else:
                    out.append(self._buf)
                    self._buf = ""
                break
        return "".join(out)

    def flush(self) -> str:
        text = "" if self._inside else self._buf
        self._buf = ""
        return text


async def chat(
    messages: list[dict], *, max_tokens: int = 4096, temperature: float = 0.2
) -> tuple[str, dict]:
    """非串流 chat（限流自動重試）。回傳 (過濾思考段後的文字, usage)。"""
    base_url, api_key, model = _chat_config()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(_MAX_ATTEMPTS):
            _record_request()
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            if resp.status_code == 200:
                break
            if attempt < _MAX_ATTEMPTS - 1 and _is_retryable(resp.text):
                await _backoff(attempt)
                continue
            break
    if resp.status_code != 200:
        raise LLMError(f"chat API {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    content = body["choices"][0]["message"].get("content") or ""
    content = _THINK_RE.sub("", content).strip()
    usage = body.get("usage") or {}
    return content, {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


def extract_json(text: str) -> dict:
    """從 LLM 回覆中抽出第一個完整 JSON 物件（容忍 ```json 圍欄與前後雜訊）。"""
    start = text.find("{")
    if start < 0:
        raise LLMError("回覆中找不到 JSON")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise LLMError("回覆中的 JSON 不完整")
