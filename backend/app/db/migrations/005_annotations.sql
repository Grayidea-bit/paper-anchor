-- Migration: 使用者標註表（T-AN-01）
-- 支援三種標註類型（underline/highlight/note）與四種顏色，可選引用 chunk_id
-- CASCADE 級聯刪除文獻時自動刪標註；SET NULL 保留標註但解除 chunk 引用

CREATE TABLE annotations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('underline', 'highlight', 'note')),
    color TEXT NOT NULL DEFAULT 'amber'
        CHECK (color IN ('amber', 'terracotta', 'sage', 'slate')),
    page INT NOT NULL,
    bbox_list JSONB NOT NULL DEFAULT '[]',
    chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
    selected_text TEXT NOT NULL DEFAULT '',
    note_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_annotations_document ON annotations(document_id);
