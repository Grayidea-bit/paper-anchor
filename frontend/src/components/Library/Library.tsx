import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./Library.module.css";
import {
  deleteDocument,
  listDocuments,
  uploadDocument,
  type Doc,
} from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";

const STATUS_LABEL: Record<Doc["status"], string> = {
  uploaded: "已上傳",
  parsing: "解析中…",
  embedding: "建立索引…",
  digesting: "產生導讀…",
  ready: "可閱讀",
  failed: "失敗",
};

const PROCESSING = new Set(["uploaded", "parsing", "embedding", "digesting"]);

export function Library() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const setDocument = useReaderStore((s) => s.setDocument);

  const refresh = useCallback(() => {
    listDocuments().then(setDocs).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(refresh, [refresh]);

  // 有文件在處理中時輪詢
  useEffect(() => {
    if (!docs.some((d) => PROCESSING.has(d.status))) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [docs, refresh]);

  const onUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      await uploadDocument(file);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const onDelete = async (id: number) => {
    try {
      await deleteDocument(id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className={styles.library}>
      <div className={styles.uploadBox}>
        <button
          className={styles.uploadBtn}
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
        >
          {uploading ? "上傳中…" : "上傳 PDF 文獻"}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onUpload(f);
          }}
        />
        {error && <p className={styles.error}>{error}</p>}
      </div>

      <ul className={styles.list}>
        {docs.map((d) => (
          <li key={d.id} className={styles.item}>
            <button
              className={styles.docBtn}
              disabled={d.status !== "ready"}
              onClick={() => setDocument(d.id)}
            >
              <span className={styles.docTitle}>{d.title || d.filename}</span>
              <span className={styles.docMeta}>
                {d.page_count > 0 ? `${d.page_count} 頁 · ` : ""}
                <span
                  className={
                    d.status === "failed"
                      ? styles.badgeFailed
                      : d.status === "ready"
                        ? styles.badgeReady
                        : styles.badgeBusy
                  }
                >
                  {STATUS_LABEL[d.status]}
                </span>
                {d.status === "failed" && d.error_msg ? ` — ${d.error_msg}` : ""}
              </span>
            </button>
            <button
              className={styles.deleteBtn}
              title="刪除"
              onClick={() => void onDelete(d.id)}
            >
              ✕
            </button>
          </li>
        ))}
        {docs.length === 0 && <p className={styles.empty}>尚無文獻，先上傳一篇吧</p>}
      </ul>
    </div>
  );
}
