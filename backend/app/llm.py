"""唯一的 LLM 供應商出入口（CLAUDE.md 鐵律 3）。

NIM 注意事項（docs/02-architecture.md D5）：
- embedding 必帶 input_type：入庫 "passage"、查詢 "query"，用錯不報錯但檢索品質劣化。
- 單筆長度受模型 512 token 上限約束，帶 truncate:"END" 保險；批量分批送出。
- chat 預設模型是推理模型：reasoning_content 直接丟棄，content 內的 <think> 段以
  ThinkFilter 過濾，只輸出最終答案。
"""

import json
import re
from collections.abc import AsyncIterator

import httpx

from app.config import get_settings

EMBED_BATCH_SIZE = 32
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(RuntimeError):
    pass


# ---------- embeddings ----------

async def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    settings = get_settings()
    results: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
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
            if resp.status_code != 200:
                raise LLMError(f"embedding API {resp.status_code}: {resp.text[:300]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            results.extend(d["embedding"] for d in data)
    return results


async def embed_passages(texts: list[str]) -> list[list[float]]:
    return await _embed(texts, "passage")


async def embed_query(text: str) -> list[float]:
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


async def chat_stream(
    messages: list[dict], *, max_tokens: int = 6144, temperature: float = 0.3
) -> AsyncIterator[dict]:
    """逐段輸出 {"type":"token","text":…}，結尾輸出 {"type":"usage",…}。

    max_tokens 對推理模型同時涵蓋思考段與答案，因此要留足（思考段可能吃掉數千 tokens）。
    """
    settings = get_settings()
    payload = {
        "model": settings.llm_chat_model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream_options": {"include_usage": True},
    }
    think = ThinkFilter()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="replace")
                raise LLMError(f"chat API {resp.status_code}: {body[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if err := event.get("error"):
                    raise LLMError(f"chat stream error: {json.dumps(err)[:300]}")
                if usage := event.get("usage"):
                    yield {
                        "type": "usage",
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    }
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                # reasoning_content（思考段）不對外輸出
                if text := delta.get("content"):
                    if cleaned := think.feed(text):
                        yield {"type": "token", "text": cleaned}
    if tail := think.flush():
        yield {"type": "token", "text": tail}


async def chat(
    messages: list[dict], *, max_tokens: int = 4096, temperature: float = 0.2
) -> tuple[str, dict]:
    """非串流 chat。回傳 (過濾思考段後的文字, usage)。"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_chat_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
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
