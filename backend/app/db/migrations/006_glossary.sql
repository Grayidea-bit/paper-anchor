-- Migration: 翻譯表（glossary）（T-TR-01）
-- 使用者圈選術語 → LLM 翻譯成設定的目標語言 → 存成該文獻的翻譯表條目（含原文錨點）
-- CASCADE 級聯刪除文獻時自動刪條目；chunk 供 AI/上下文用，刪 chunk 時 SET NULL 不連坐

CREATE TABLE glossary_entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    translation TEXT NOT NULL DEFAULT '',
    target_lang TEXT NOT NULL,
    page INT NOT NULL,
    bbox_list JSONB NOT NULL DEFAULT '[]',
    chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_glossary_document ON glossary_entries(document_id);
