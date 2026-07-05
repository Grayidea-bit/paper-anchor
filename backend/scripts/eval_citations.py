"""引用命中率測試集（M3 DoD；M5 加 scope 隔離驗證）。在 api 容器內執行：

    python -m scripts.eval_citations                  # 單篇模式：每篇 5 問
    python -m scripts.eval_citations --scope project  # 專案模式：隔離鐵證

單篇模式檢查：
  A. 回答非空
  B. 至少 1 個結構化引用（除非回答明確表示「文獻中未提及」）
  C. 引用的 page 在文獻頁數範圍內、bbox_list 非空（可高亮）
專案模式：建臨時專案、指派一半 ready 文獻、跨文獻問答，額外檢查
  B'. 每個 citation 的 document_id ∈ 專案文獻集合（SQL 隔離鐵證）
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
                1 <= c["page"] <= doc["page_count"] and c["bbox_list"] for c in r["citations"]
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


PROJECT_QUESTIONS = [
    "這些論文各自要解決什麼問題？有何異同？",
    "比較這些論文的方法設計，它們的核心差異在哪？",
    "綜合來看，這些工作共同指向什麼研究趨勢或限制？",
]


async def eval_project_scope(client: httpx.AsyncClient) -> list[dict]:
    """臨時專案 + 跨文獻問答 + 隔離鐵證（citation.document_id ∈ 專案集合）。"""
    docs = [d for d in (await client.get(f"{BASE}/api/documents")).json() if d["status"] == "ready"]
    if len(docs) < 2:
        print("need >= 2 ready docs for project eval")
        sys.exit(1)
    in_project = docs[: max(2, len(docs) // 2 + 1)]
    page_count = {d["id"]: d["page_count"] for d in docs}
    project = (await client.post(f"{BASE}/api/projects", json={"name": "citation-eval-tmp"})).json()
    pid = project["id"]
    results = []
    try:
        for d in in_project:
            await client.patch(f"{BASE}/api/documents/{d['id']}", json={"project_id": pid})
        allowed = {d["id"] for d in in_project}
        print(f"project {pid}: docs {sorted(allowed)} of {sorted(page_count)}", flush=True)
        conv = (
            await client.post(f"{BASE}/api/projects/{pid}/conversations", json={"title": "eval"})
        ).json()
        for q in PROJECT_QUESTIONS:
            r = await ask(client, conv["id"], q)
            cited_docs = {c.get("document_id") for c in r["citations"]}
            checks = {
                "A_answer": bool(r["answer"].strip()) and not r["error"],
                "B_cited": bool(r["citations"]),
                "B_isolated": cited_docs <= allowed,
                "C_anchors": all(
                    c.get("document_id") in page_count
                    and 1 <= c["page"] <= page_count[c["document_id"]]
                    and c["bbox_list"]
                    for c in r["citations"]
                ),
            }
            results.append(
                {
                    "q": q[:20],
                    "ok": all(checks.values()),
                    "checks": checks,
                    "cited_docs": sorted(x for x in cited_docs if x),
                    "err": r["error"],
                }
            )
            status = "PASS" if results[-1]["ok"] else "FAIL"
            print(f"[project] {status} docs={sorted(cited_docs)} {q[:24]}", flush=True)
    finally:
        await client.delete(f"{BASE}/api/projects/{pid}")
    return results


async def main() -> None:
    project_mode = "--scope" in sys.argv and "project" in sys.argv
    async with httpx.AsyncClient(timeout=300) as client:
        if project_mode:
            all_results = await eval_project_scope(client)
        else:
            docs = [
                d
                for d in (await client.get(f"{BASE}/api/documents")).json()
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
