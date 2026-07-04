import { useState } from "react";
import styles from "./ChatPane.module.css";
import { getChunks, type Chunk } from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";

/** M2 實作 SSE 對話與引用渲染；目前提供 M1 DoD 的引用高亮驗證工具 */
export function ChatPane() {
  const documentId = useReaderStore((s) => s.documentId);
  const jumpTo = useReaderStore((s) => s.jumpTo);
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [lastChunk, setLastChunk] = useState<Chunk | null>(null);
  const [error, setError] = useState<string | null>(null);

  const testHighlight = async () => {
    if (documentId === null) return;
    setError(null);
    try {
      const list = chunks ?? (await getChunks(documentId));
      setChunks(list);
      if (list.length === 0) {
        setError("此文獻沒有任何 chunk");
        return;
      }
      const pick = list[Math.floor(Math.random() * list.length)];
      setLastChunk(pick);
      jumpTo({ page: pick.page, bboxList: pick.bbox_list });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className={styles.pane} aria-label="對話面板">
      <div className={styles.messages}>
        {documentId === null ? (
          <p className={styles.hint}>上傳文獻後，可在此與 LLM 討論內容</p>
        ) : (
          <div className={styles.devBox}>
            <p className={styles.hint}>對話功能將於 M2 開放。</p>
            <button className={styles.devBtn} onClick={() => void testHighlight()}>
              🎯 測試引用高亮（隨機 chunk）
            </button>
            {lastChunk && (
              <div className={styles.chunkPreview}>
                <p className={styles.chunkMeta}>
                  chunk #{lastChunk.chunk_index} · 第 {lastChunk.page} 頁 ·{" "}
                  {lastChunk.bbox_list.length} 個區塊
                </p>
                <p className={styles.chunkText}>
                  {lastChunk.content.slice(0, 180)}
                  {lastChunk.content.length > 180 ? "…" : ""}
                </p>
              </div>
            )}
            {error && <p className={styles.error}>{error}</p>}
          </div>
        )}
      </div>
      <div className={styles.inputRow}>
        <textarea
          className={styles.input}
          placeholder="M2 開放提問…"
          rows={2}
          disabled
        />
        <button className={styles.send} disabled>
          送出
        </button>
      </div>
    </section>
  );
}
