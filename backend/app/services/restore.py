"""從 Google Drive 匯入還原（M13 D11 / T-RS-01）。

把遠端 manifest 指向的一次完整備份**合併**回本機 DB，並對新文獻重跑 ingest（解析→切塊→
嵌入）以重建引用鏈。設計目標（見 D11）：新機還原可完整重現、舊機重跑不破壞本地較新資料、
任何中斷重跑都收斂（冪等）。

合併總原則：不刪本地任何列；所有主鍵重生並在關聯欄位 remap；可比時間戳時新者勝、無從
比較時本地優先；`settings.json` 一律不還原。身分簽章（非備份端 id）判斷本地是否已存在
對應列。與 backup **共用同一把服務層鎖**（`backup.try_begin`），天然互斥。

實際 phase 順序（D11 語意為準，實作順序見下）：
    download（manifest + db dumps）→ download（新文獻 PDF，見 M15 T-FD-06：無 session
    階段全部下載完才開 merge 的 session，避免數十秒的 Drive 網路 I/O 占住 DB pool 連線，
    對照 run_backup 刻意短開 session 的慣例）→ merge（純 DB：projects → documents[insert
    + remap] → annotations → glossary → conversations+messages）→ ingest（逐篇序列，
    n/m 進度）。最耗時的重嵌集中在 merge 完成後的 ingest phase 慢慢跑並回報進度。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError
from app.services import backup, gdrive
from app.services.ingest import ingest_document

logger = logging.getLogger(__name__)

# 逐表處理順序：先父後子（projects→documents 建 remap，才能接續 annotations/... 的關聯）。
_DUMP_TABLES: tuple[str, ...] = (
    "projects",
    "documents",
    "annotations",
    "glossary_entries",
    "conversations",
    "messages",
)


def _empty_summary() -> dict[str, Any]:
    return {
        "documents_new": 0,
        "documents_skipped": 0,
        "annotations_new": 0,
        "annotations_updated": 0,
        "glossary_new": 0,
        "conversations_new": 0,
        "messages_new": 0,
        "ingest_failed": [],
    }


# ---------- 值正規化 helpers ----------


def _as_json(value: Any) -> Any:
    """JSONB 欄位在不同 DB 驅動可能回字串或已解析物件；統一成 Python 物件。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _parse_dt(value: Any) -> datetime | None:
    """把時間戳（isoformat 字串**或 datetime 物件**）正規化成 aware UTC datetime；不可比回 None。

    比較點（conversations 身分簽章、annotations newer-wins）兩側來源不同：dump 端是 ISO
    字串，本地端依 DB 驅動而異——asyncpg TIMESTAMPTZ 回 aware datetime、SQLite 回字串、
    無 TZ 的 TIMESTAMP 可能 naive datetime，三種都須收斂到同一表示再比（T-RS-03 真
    Postgres E2E 抓到「字串 vs datetime 不相等」的冪等破口）。字串容忍空格/`T` 分隔與
    `Z` 結尾；naive 一律視為 UTC。
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


def _bbox_key(bbox_list: Any) -> tuple:
    """把 bbox_list 正規化成可雜湊的簽章（四捨五入抑制跨 DB round-trip 浮點噪音）。"""
    data = _as_json(bbox_list) or []
    try:
        return tuple(tuple(round(float(x), 2) for x in box) for box in data)
    except (TypeError, ValueError):
        return ()


# ---------- staging ----------


def _prepare_restore_staging() -> Path:
    """建立乾淨的還原暫存目錄（含 db/ 子目錄）；資料目錄旁的 restore_staging。"""
    upload_dir = Path(get_settings().upload_dir)
    return backup.prepare_staging(upload_dir.parent / "restore_staging")


def _load_dump(staging: Path, name: str) -> list[dict]:
    path = staging / "db" / name
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


# ---------- phase: download ----------


async def _download_phase(staging: Path) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """定位遠端備份、下載 manifest 與 db dumps，回傳 (dumps, remote_pdf_map)。

    遠端無 manifest → 400 no_backup；manifest.format_version 不符 → 400 unsupported_format。
    """
    backup.set_progress("download", 0, 1)
    root_id = await gdrive.ensure_folder(backup.BACKUP_FOLDER_NAME)
    root_files = await gdrive.list_folder(root_id)
    manifest_entry = next((f for f in root_files if f["name"] == "manifest.json"), None)
    if manifest_entry is None:
        raise AppError("no_backup", "遠端找不到備份（缺 manifest.json）", status=400)

    db_id = await gdrive.ensure_folder("db", parent_id=root_id)
    pdfs_id = await gdrive.ensure_folder("pdfs", parent_id=root_id)
    db_by_name = {f["name"]: f["id"] for f in await gdrive.list_folder(db_id)}

    downloads: list[tuple[str, Path]] = [(manifest_entry["id"], staging / "manifest.json")]
    for table in _DUMP_TABLES:
        name = f"{table}.json"
        if name in db_by_name:
            downloads.append((db_by_name[name], staging / "db" / name))

    total = len(downloads)
    backup.set_progress("download", 0, total)
    for i, (file_id, dest) in enumerate(downloads, start=1):
        await gdrive.download_file(file_id, dest)
        backup.set_progress("download", i, total)

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != backup.FORMAT_VERSION:
        raise AppError(
            "unsupported_format",
            f"備份格式版本不支援：{manifest.get('format_version')}",
            status=400,
        )

    dumps = {table: _load_dump(staging, f"{table}.json") for table in _DUMP_TABLES}
    remote_pdf_map = {f["name"]: f["id"] for f in await gdrive.list_folder(pdfs_id)}
    return dumps, remote_pdf_map


# ---------- phase: merge ----------


async def _merge_projects(session: AsyncSession, dump_projects: list[dict]) -> dict[int, int]:
    """依 name 簽章 remap/插入專案，回傳 {dump_project_id: local_id}。"""
    existing = {p["name"]: p["id"] for p in await repo.list_projects(session)}
    remap: dict[int, int] = {}
    for p in dump_projects:
        name = p["name"]
        local_id = existing.get(name)
        if local_id is None:
            local_id = await repo.restore_insert_project(
                session, name=name, created_at=p["created_at"]
            )
            existing[name] = local_id
        remap[p["id"]] = local_id
    return remap


async def _download_new_document_pdfs(
    dump_docs: list[dict],
    remote_pdf_map: dict[str, str],
) -> dict[str, dict]:
    """無 session 階段：查本地既有文獻 UUID、下載新文獻的 PDF 到 upload_dir。

    Drive PDF 下載一篇可能數十秒，過去併在 merge 的單一 DB session 內做，會白白占住
    連線池數十秒（對照 `run_backup` 刻意短開 session 的慣例，見 M15 T-FD-06 審查發現）。
    這裡先用一個短開的 session 讀本地文獻清單、關閉，再在無 session 狀態下逐一下載，
    merge phase 開的 session 就只剩純 DB 寫入。

    回傳 {uuid_name: local_document_dict}（本地既有文獻依 UUID 檔名索引），供
    `_merge_documents` 直接判斷存在與否，不必在 merge session 內重查一次。
    """
    async with SessionLocal() as session:
        local_docs = await repo.dump_table_rows(session, "documents")
    local_by_uuid = {Path(d["file_path"]).name: d for d in local_docs if d.get("file_path")}

    upload_dir = Path(get_settings().upload_dir)
    await asyncio.to_thread(upload_dir.mkdir, parents=True, exist_ok=True)

    pending: list[tuple[str, str]] = []
    for d in dump_docs:
        uuid_name = Path(d.get("file_path") or "").name
        if uuid_name and uuid_name not in local_by_uuid and uuid_name in remote_pdf_map:
            pending.append((uuid_name, remote_pdf_map[uuid_name]))

    # 進度沿用既有 "download" phase 語意（下載階段，見 docs/02-architecture.md §5）：
    # 使用者體感上這仍是「下載中」，只是這次下載的是新文獻 PDF 而非 manifest/db dump。
    total = len(pending)
    backup.set_progress("download", 0, total)
    for i, (uuid_name, file_id) in enumerate(pending, start=1):
        await gdrive.download_file(file_id, upload_dir / uuid_name)
        backup.set_progress("download", i, total)

    return local_by_uuid


async def _merge_documents(
    session: AsyncSession,
    dump_docs: list[dict],
    project_remap: dict[int, int],
    remote_pdf_map: dict[str, str],
    local_by_uuid: dict[str, dict],
    summary: dict[str, Any],
    ingest_jobs: list[tuple[int, bool, str]],
) -> dict[int, int]:
    """依 PDF UUID 檔名（file_path basename）簽章處理文獻，回傳 {dump_doc_id: local_id}。

    純 DB 合併：PDF 已在 `_download_new_document_pdfs`（無 session 階段）下載完成，這裡
    不再有網路 I/O，session 只做寫入。

    - 已存在（UUID 命中本地，`local_by_uuid`）：remap，整篇跳過；本地 status 為 failed
      或 transient 殘態（parsing/embedding，程序中途被殺留下的半殘狀態，見 M15 T-FD-01）
      → delete_chunks 後排入重嵌（重跑即修復，D11）。
    - 不存在：遠端 pdfs/ 有對應檔才是新文獻——PDF 已下載好 → restore_insert_document →
      排入 ingest（run_digest = dump 無 digest）；遠端缺 PDF 整篇跳過記 documents_skipped。
    """
    upload_dir = Path(get_settings().upload_dir)

    remap: dict[int, int] = {}
    digest_fixups: list[tuple[int, dict]] = []
    for d in dump_docs:
        file_path = d.get("file_path") or ""
        uuid_name = Path(file_path).name
        title = d.get("title") or uuid_name
        digest = _as_json(d.get("digest"))
        token_usage = _as_json(d.get("token_usage")) or {}

        local = local_by_uuid.get(uuid_name)
        if local is not None:
            remap[d["id"]] = local["id"]
            if local.get("status") in ("failed", "parsing", "embedding"):
                # 修復路徑：failed 或 transient 殘態皆清殘塊後重嵌
                # （保留使用者本地標註，故不刪文獻本身）。
                await repo.delete_chunks(session, local["id"])
                ingest_jobs.append((local["id"], True, title))
            continue

        if uuid_name not in remote_pdf_map:
            summary["documents_skipped"] += 1
            continue

        dest = upload_dir / uuid_name
        dump_pid = d.get("project_id")
        new_id = await repo.restore_insert_document(
            session,
            project_id=project_remap.get(dump_pid) if dump_pid is not None else None,
            title=d.get("title") or "",
            filename=d.get("filename") or uuid_name,
            file_path=str(dest),
            digest=digest,
            token_usage=token_usage,
            created_at=d["created_at"],
        )
        remap[d["id"]] = new_id
        summary["documents_new"] += 1
        ingest_jobs.append((new_id, not digest, title))
        if isinstance(digest, dict):
            digest_fixups.append((new_id, digest))

    # digest citations 的 document_id remap 需要**完整** remap 表（含自身與後續篇的新 id），
    # 故等 documents 全部處理完再回寫；無變動（citations 不帶 document_id）就不多寫一次。
    for new_id, digest in digest_fixups:
        remapped = _remap_digest(digest, remap)
        if remapped != digest:
            await repo.restore_update_document_digest(session, new_id, remapped)
    return remap


async def _merge_annotations(
    session: AsyncSession,
    dump_anns: list[dict],
    doc_remap: dict[int, int],
    summary: dict[str, Any],
) -> None:
    """身分簽章 (document, type, page, bbox)。存在→比 updated_at 新者覆蓋；否則插入。"""
    cache: dict[int, dict[tuple, dict[str, Any]]] = {}

    async def _index_for(local_doc_id: int) -> dict[tuple, dict[str, Any]]:
        if local_doc_id not in cache:
            idx: dict[tuple, dict[str, Any]] = {}
            for a in await repo.list_annotations(session, local_doc_id):
                sig = (a["type"], a["page"], _bbox_key(a["bbox_list"]))
                idx[sig] = {"id": a["id"], "updated_at": _parse_dt(a["updated_at"])}
            cache[local_doc_id] = idx
        return cache[local_doc_id]

    for a in dump_anns:
        local_doc_id = doc_remap.get(a["document_id"])
        if local_doc_id is None:
            continue  # 文獻被跳過（缺 PDF）→ 附屬標註一併跳過
        idx = await _index_for(local_doc_id)
        sig = (a["type"], a["page"], _bbox_key(a["bbox_list"]))
        dump_dt = _parse_dt(a.get("updated_at"))
        existing = idx.get(sig)
        if existing is not None:
            local_dt = existing["updated_at"]
            if dump_dt is not None and (local_dt is None or dump_dt > local_dt):
                await repo.restore_overwrite_annotation(
                    session,
                    existing["id"],
                    note_text=a.get("note_text") or "",
                    color=a["color"],
                    selected_text=a.get("selected_text") or "",
                    updated_at=a["updated_at"],
                )
                existing["updated_at"] = dump_dt
                summary["annotations_updated"] += 1
            continue
        new_id = await repo.restore_insert_annotation(
            session,
            document_id=local_doc_id,
            type=a["type"],
            color=a["color"],
            page=a["page"],
            bbox_list=_as_json(a["bbox_list"]) or [],
            selected_text=a.get("selected_text") or "",
            note_text=a.get("note_text") or "",
            created_at=a["created_at"],
            updated_at=a.get("updated_at") or a["created_at"],
        )
        idx[sig] = {"id": new_id, "updated_at": dump_dt}
        summary["annotations_new"] += 1


async def _merge_glossary(
    session: AsyncSession,
    dump_glos: list[dict],
    doc_remap: dict[int, int],
    summary: dict[str, Any],
) -> None:
    """身分簽章 (document, term, target_lang, page)。無 updated_at 可比 → 存在即跳過。"""
    cache: dict[int, set[tuple]] = {}

    async def _index_for(local_doc_id: int) -> set[tuple]:
        if local_doc_id not in cache:
            sigs = {
                (g["term"], g["target_lang"], g["page"])
                for g in await repo.list_glossary_entries(session, local_doc_id)
            }
            cache[local_doc_id] = sigs
        return cache[local_doc_id]

    for g in dump_glos:
        local_doc_id = doc_remap.get(g["document_id"])
        if local_doc_id is None:
            continue
        idx = await _index_for(local_doc_id)
        sig = (g["term"], g["target_lang"], g["page"])
        if sig in idx:
            continue
        await repo.restore_insert_glossary_entry(
            session,
            document_id=local_doc_id,
            term=g["term"],
            translation=g.get("translation") or "",
            target_lang=g["target_lang"],
            page=g["page"],
            bbox_list=_as_json(g["bbox_list"]) or [],
            notes=g.get("notes") or "",
            created_at=g["created_at"],
        )
        idx.add(sig)
        summary["glossary_new"] += 1


def _remap_citations(citations: Any, doc_remap: dict[int, int]) -> list:
    """只 remap 每個 citation 的 document_id（查無→null）；其餘欄位原樣（訊息內自洽，D11）。"""
    out: list = []
    for c in citations or []:
        if not isinstance(c, dict):
            out.append(c)
            continue
        nc = dict(c)
        if "document_id" in nc:
            nc["document_id"] = doc_remap.get(nc["document_id"])
        out.append(nc)
    return out


def _remap_digest(digest: dict, doc_remap: dict[int, int]) -> dict:
    """digest 各 section 的 citations 套用與 messages 相同的 document_id remap（查無→null）。

    dump 的 digest 內 citations 若帶備份端舊 document_id，撞上本機另一篇的 id 會讓導讀
    面板跳錯文獻——與 messages 路徑同一套 `_remap_citations` 語意收斂（T-RS-03 審查發現）。
    """
    sections = digest.get("sections")
    if not isinstance(sections, list):
        return digest
    new_sections: list = []
    for s in sections:
        if isinstance(s, dict) and isinstance(s.get("citations"), list):
            s = {**s, "citations": _remap_citations(s["citations"], doc_remap)}
        new_sections.append(s)
    return {**digest, "sections": new_sections}


async def _merge_conversations(
    session: AsyncSession,
    dump_convs: list[dict],
    dump_msgs: list[dict],
    doc_remap: dict[int, int],
    project_remap: dict[int, int],
    summary: dict[str, Any],
) -> None:
    """身分簽章 (scope, remap 後目標, title, created_at)。存在→整串跳過；否則整串匯入。"""
    msgs_by_conv: dict[int, list[dict]] = {}
    for m in dump_msgs:
        msgs_by_conv.setdefault(m["conversation_id"], []).append(m)

    index_cache: dict[tuple, dict[tuple, int]] = {}

    for conv in dump_convs:
        scope = conv["scope"]
        target_doc: int | None = None
        target_proj: int | None = None
        if scope == "document":
            target_doc = doc_remap.get(conv.get("document_id"))
            if target_doc is None:
                continue  # 文獻被跳過 → 對話一併跳過
        elif scope == "project":
            target_proj = project_remap.get(conv.get("project_id"))
            if target_proj is None:
                continue

        cache_key = (scope, target_doc, target_proj)
        if cache_key not in index_cache:
            existing = await repo.list_conversations_scoped(
                session, scope=scope, document_id=target_doc, project_id=target_proj
            )
            index_cache[cache_key] = {
                (c["title"], _parse_dt(c["created_at"])): c["id"] for c in existing
            }
        idx = index_cache[cache_key]
        sig = (conv["title"], _parse_dt(conv["created_at"]))
        if sig in idx:
            continue

        new_conv_id = await repo.restore_insert_conversation(
            session,
            scope=scope,
            document_id=target_doc,
            project_id=target_proj,
            title=conv["title"],
            model=conv.get("model"),
            created_at=conv["created_at"],
        )
        idx[sig] = new_conv_id
        summary["conversations_new"] += 1

        for m in msgs_by_conv.get(conv["id"], []):
            await repo.restore_insert_message(
                session,
                conversation_id=new_conv_id,
                role=m["role"],
                content=m["content"],
                citations=_remap_citations(_as_json(m.get("citations")), doc_remap),
                selection=_as_json(m.get("selection")),
                token_usage=_as_json(m.get("token_usage")) or {},
                created_at=m["created_at"],
            )
            summary["messages_new"] += 1


async def _merge_phase(
    dumps: dict[str, list[dict]],
    remote_pdf_map: dict[str, str],
    local_by_uuid: dict[str, dict],
    summary: dict[str, Any],
) -> list[tuple[int, bool, str]]:
    """在單一 session 內逐表合併，回傳待 ingest 的工作清單 (local_doc_id, run_digest, title)。

    新文獻 PDF 已在呼叫前的 `_download_new_document_pdfs` 下載完畢（無 session 階段），
    這裡的 session 全程只做 DB 寫入，不再被下載占住連線（M15 T-FD-06）。
    """
    backup.set_progress("merge", 0, 1)
    ingest_jobs: list[tuple[int, bool, str]] = []
    async with SessionLocal() as session:
        project_remap = await _merge_projects(session, dumps["projects"])
        doc_remap = await _merge_documents(
            session,
            dumps["documents"],
            project_remap,
            remote_pdf_map,
            local_by_uuid,
            summary,
            ingest_jobs,
        )
        await _merge_annotations(session, dumps["annotations"], doc_remap, summary)
        await _merge_glossary(session, dumps["glossary_entries"], doc_remap, summary)
        await _merge_conversations(
            session, dumps["conversations"], dumps["messages"], doc_remap, project_remap, summary
        )
    backup.set_progress("merge", 1, 1)
    return ingest_jobs


# ---------- phase: ingest ----------


async def _ingest_phase(ingest_jobs: list[tuple[int, bool, str]], summary: dict[str, Any]) -> None:
    """逐篇序列重嵌（兼進度 n/m）；單篇失敗記 summary.ingest_failed 續跑（D11）。"""
    total = len(ingest_jobs)
    for i, (doc_id, run_digest, title) in enumerate(ingest_jobs, start=1):
        backup.set_progress("ingest", i, total)
        try:
            await ingest_document(doc_id, run_digest=run_digest)
        except Exception:
            logger.exception("restore: ingest 拋出例外 doc=%s", doc_id)
            summary["ingest_failed"].append(title)
            continue
        # ingest_document 內部吞例外並標 failed；查狀態補記 ingest_failed。
        async with SessionLocal() as session:
            doc = await repo.get_document(session, doc_id)
        if doc is not None and doc["status"] == "failed":
            summary["ingest_failed"].append(title)


# ---------- 編排 ----------


def _safe_error_message(exc: Exception) -> str:
    """轉成可持久化/可顯示的錯誤訊息；`AppError`（含 no_backup 等）訊息已不含秘密可直接用。"""
    if isinstance(exc, AppError):
        return exc.message
    logger.exception("restore: run_restore 發生未預期例外")
    return "還原失敗，請查看伺服器日誌"


async def _record_result(
    *, ok: bool, summary: dict[str, Any] | None = None, error: str | None = None
) -> None:
    result: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "ok": ok}
    if summary is not None:
        result["summary"] = summary
    if error is not None:
        result["error"] = error
    backup._last_restore = result
    await settings_store.update({"restore_last_run": result})


async def run_restore() -> None:
    """還原主編排；由 BackgroundTask 呼叫。與 backup 共用同一把鎖（進行中則直接略過）。

    結果（含 summary 或 error）寫入 `restore_last_run` 並更新記憶體 `_last_restore`，由
    `GET /api/backup/status` 的 `last_restore` 輪詢。staging 一律清理。
    """
    async with backup.try_begin("restore") as acquired:
        if not acquired:
            return
        staging = _prepare_restore_staging()
        summary = _empty_summary()
        try:
            dumps, remote_pdf_map = await _download_phase(staging)
            local_by_uuid = await _download_new_document_pdfs(dumps["documents"], remote_pdf_map)
            ingest_jobs = await _merge_phase(dumps, remote_pdf_map, local_by_uuid, summary)
            await _ingest_phase(ingest_jobs, summary)
            await _record_result(ok=True, summary=summary)
        except Exception as exc:
            await _record_result(ok=False, error=_safe_error_message(exc))
        finally:
            backup.cleanup_staging(staging)
