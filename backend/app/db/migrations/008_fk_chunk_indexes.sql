-- Migration: annotations/glossary_entries 的 chunk_id 補索引（M15 T-FD-06）
-- 兩欄皆為 ON DELETE SET NULL 的 FK；刪 chunks（ingest 重跑先 delete_chunks、還原
-- 修復路徑亦同）時 Postgres 需要找出所有引用列才能 SET NULL，無索引則全表掃描。

CREATE INDEX IF NOT EXISTS idx_annotations_chunk ON annotations(chunk_id);
CREATE INDEX IF NOT EXISTS idx_glossary_chunk ON glossary_entries(chunk_id);
