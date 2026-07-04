import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./Library.module.css";
import {
  deleteDocument,
  listDocuments,
  uploadDocument,
  type Doc,
} from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";
import { useT } from "../../i18n";

const PROCESSING = new Set(["uploaded", "parsing", "embedding", "digesting"]);

export function Library() {
  const t = useT();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const setDocument = useReaderStore((s) => s.setDocument);

  const statusLabel = (s: Doc["status"]) => t[`status_${s}` as const];

  const refresh = useCallback(() => {
    listDocuments().then(setDocs).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(refresh, [refresh]);

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
          {uploading ? t.uploading : t.upload}
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
        {docs.map((d, i) => (
          <li key={d.id} className={styles.item} style={{ animationDelay: `${i * 60}ms` }}>
            <button
              className={styles.docBtn}
              disabled={d.status !== "ready"}
              onClick={() => setDocument(d.id)}
            >
              <span className={styles.docTitle}>{d.title || d.filename}</span>
              <span className={styles.docMeta}>
                {d.page_count > 0 && (
                  <span className={styles.metaPages}>
                    {d.page_count} {t.pages}
                  </span>
                )}
                <span className={styles.badge} data-status={d.status}>
                  {statusLabel(d.status)}
                </span>
                {d.status === "failed" && d.error_msg && (
                  <span className={styles.errNote}>{d.error_msg}</span>
                )}
              </span>
            </button>
            <button
              className={styles.deleteBtn}
              title={t.delete}
              onClick={() => void onDelete(d.id)}
            >
              ✕
            </button>
          </li>
        ))}
        {docs.length === 0 && <p className={styles.empty}>{t.emptyLibrary}</p>}
      </ul>
    </div>
  );
}
