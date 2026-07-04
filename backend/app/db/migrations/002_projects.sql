-- M5：專案分類 + 對話 scope（docs/02-architecture.md D6）

CREATE TABLE projects (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 文獻歸屬：刪專案 → 文獻回未分類
ALTER TABLE documents ADD COLUMN project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL;
CREATE INDEX idx_documents_project ON documents(project_id);

-- 對話三態：document（現有）/ project / library
ALTER TABLE conversations ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE conversations ADD COLUMN project_id BIGINT REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE conversations ADD COLUMN scope TEXT NOT NULL DEFAULT 'document'
    CHECK (scope IN ('document', 'project', 'library'));
ALTER TABLE conversations ADD CONSTRAINT chk_conversations_scope CHECK (
    (scope = 'document' AND document_id IS NOT NULL AND project_id IS NULL)
    OR (scope = 'project' AND project_id IS NOT NULL AND document_id IS NULL)
    OR (scope = 'library' AND document_id IS NULL AND project_id IS NULL)
);
CREATE INDEX idx_conversations_project ON conversations(project_id);
