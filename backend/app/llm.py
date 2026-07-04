"""唯一的 LLM 供應商出入口（CLAUDE.md 鐵律 3）。

NIM 注意事項（docs/02-architecture.md D5）：
- embedding 必帶 input_type：入庫 "passage"、查詢 "query"，用錯不報錯但檢索品質劣化。
- 單筆長度受模型 512 token 上限約束，帶 truncate:"END" 保險；批量分批送出。
"""

import httpx

from app.config import get_settings

EMBED_BATCH_SIZE = 32
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class LLMError(RuntimeError):
    pass


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
