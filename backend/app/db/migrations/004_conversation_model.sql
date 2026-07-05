-- M9：對話持久化選用模型（每對話記住，取代後端固定讀值）
ALTER TABLE conversations ADD COLUMN model TEXT;
