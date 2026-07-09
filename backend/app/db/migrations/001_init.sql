CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    page_count INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'uploaded'
        CHECK (status IN ('uploaded', 'parsing', 'embedding', 'digesting', 'ready', 'failed')),
    error_msg TEXT,
    digest JSONB,
    token_usage JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_documents_user ON documents(user_id);

CREATE TABLE chunks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    page INT NOT NULL,
    section TEXT,
    content TEXT NOT NULL,
    bbox_list JSONB NOT NULL DEFAULT '[]',
    embedding VECTOR(1024),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX idx_chunks_document ON chunks(document_id);
-- 刻意未建 ANN 索引（ivfflat/hnsw）：僅 document scope 精確掃描即夠；
-- library/project scope 為全庫精確掃描，chunk 總數破 ~2 萬時應建（門檻卡見 docs/03-roadmap.md M15）

CREATE TABLE conversations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '新對話',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conversations_document ON conversations(document_id);

CREATE TABLE messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    citations JSONB NOT NULL DEFAULT '[]',
    selection JSONB,
    token_usage JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conversation ON messages(conversation_id);

-- MVP 單一使用者
INSERT INTO users (email) VALUES ('default@local');
