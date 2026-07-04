import { useEffect, useState } from "react";
import styles from "./App.module.css";
import { ChatPane } from "./components/ChatPane/ChatPane";
import { PDFPane } from "./components/PDFPane/PDFPane";
import { getDocument, getHealth, type Doc, type Health } from "./api/client";
import { useReaderStore } from "./stores/readerStore";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const documentId = useReaderStore((s) => s.documentId);
  const setDocument = useReaderStore((s) => s.setDocument);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    if (documentId === null) {
      setDoc(null);
      return;
    }
    getDocument(documentId).then(setDoc).catch(() => setDoc(null));
  }, [documentId]);

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.left}>
          <h1 className={styles.title}>AI 文獻導讀</h1>
          {documentId !== null && (
            <>
              <button className={styles.backBtn} onClick={() => setDocument(null)}>
                ← 文獻庫
              </button>
              <span className={styles.docTitle}>{doc?.title ?? ""}</span>
            </>
          )}
        </div>
        <span className={styles.status}>
          {health === null ? "API 未連線" : `API ✓ / DB ${health.db ? "✓" : "✗"}`}
        </span>
      </header>
      <main className={styles.panes}>
        <PDFPane />
        <ChatPane />
      </main>
    </div>
  );
}
