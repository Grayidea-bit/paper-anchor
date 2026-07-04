"""引用命中率測試集（M3 DoD）。在 api 容器內執行：

    python -m scripts.eval_citations

對每篇 ready 文獻問 5 個標準問題，檢查：
  A. 回答非空
  B. 至少 1 個結構化引用（除非回答明確表示「文獻中未提及」）
  C. 引用的 page 在文獻頁數範圍內、bbox_list 非空（可高亮）
位置正確性（高亮框住對的段落）由人工抽查，本腳本保證機器可驗的部分。
"""

import asyncio
import json
import sys

import httpx

BASE = "http://localhost:8000"
QUESTIONS = [
    "這篇論文要解決的核心問題是什麼？",
    "論文提出的方法或系統的主要組成是什麼？",
    "論文在哪些資料集或基準上評估？主要結果如何？",
    "這篇論文的主要貢獻有哪些？",
    "論文自述或可推知的限制是什麼？",
]
NOT_FOUND_MARKERS = ["未提及", "未明確", "not mention", "does not mention"]


async def ask(client: httpx.AsyncClient, conv_id: int, question: str) -> dict:
    answer, citations, error = [], [], None
    async with client.stream(
        "POST",
        f"{BASE}/api/conversations/{conv_id}/messages",
        json={"content": question},
        timeout=300,
    ) as resp:
        event = ""
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if event == "token":
                    answer.append(data["text"])
                elif event == "citations":
                    citations = data["citations"]
                elif event == "error":
                    error = data["message"]
    return {"answer": "".join(answer), "citations": citations, "error": error}


async def eval_doc(client: httpx.AsyncClient, doc: dict) -> list[dict]:
    conv = (
        await client.post(
            f"{BASE}/api/documents/{doc['id']}/conversations",
            json={"title": "citation-eval"},
        )
    ).json()
    results = []
    for q in QUESTIONS:
        r = await ask(client, conv["id"], q)
        says_not_found = any(m in r["answer"].lower() for m in NOT_FOUND_MARKERS)
        checks = {
            "A_answer": bool(r["answer"].strip()) and not r["error"],
            "B_cited": bool(r["citations"]) or says_not_found,
            "C_anchors": all(
                1 <= c["page"] <= doc["page_count"] and c["bbox_list"]
                for c in r["citations"]
            ),
        }
        results.append(
            {
                "doc": doc["id"],
                "q": q[:18],
                "ok": all(checks.values()),
                "checks": checks,
                "n_cites": len(r["citations"]),
                "err": r["error"],
            }
        )
        status = "PASS" if results[-1]["ok"] else "FAIL"
        print(f"[doc{doc['id']}] {status} cites={len(r['citations'])} {q[:24]}", flush=True)
    return results


async def main() -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        docs = [
            d for d in (await client.get(f"{BASE}/api/documents")).json()
            if d["status"] == "ready"
        ]
        print(f"evaluating {len(docs)} docs x {len(QUESTIONS)} questions", flush=True)
        all_results = []
        for doc in docs:  # 序列執行，避免 NIM 限流
            all_results.extend(await eval_doc(client, doc))
    passed = sum(1 for r in all_results if r["ok"])
    print(f"\nRESULT: {passed}/{len(all_results)} passed")
    for r in all_results:
        if not r["ok"]:
            print("  FAIL:", json.dumps(r, ensure_ascii=False))
    sys.exit(0 if passed == len(all_results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
