-- Migration: 翻譯表條目補充註解欄位（T-TR-04）
-- 從對話「翻譯」動作的詳細翻譯全文萃取簡潔譯文時，順帶保留一到兩句白話註解；
-- 原「直接圈選加入」路徑（不帶 source_text）不產生註解，notes 為空字串。

ALTER TABLE glossary_entries ADD COLUMN notes TEXT NOT NULL DEFAULT '';
