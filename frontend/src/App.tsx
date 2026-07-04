import { useEffect, useState } from "react";
import styles from "./App.module.css";
import { ChatPane } from "./components/ChatPane/ChatPane";
import { PDFPane } from "./components/PDFPane/PDFPane";
import { getDocument, getHealth, type Doc, type Health } from "./api/client";
import { useReaderStore } from "./stores/readerStore";
import { useT, useUiStore, type Lang } from "./i18n";

export default function App() {
  const t = useT();
  const lang = useUiStore((s) => s.lang);
  const setLang = useUiStore((s) => s.setLang);
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
          <h1 className={styles.wordmark}>
            {t.appName}
            <span className={styles.wordmarkRule} />
          </h1>
          {documentId !== null && (
            <>
              <button className={styles.backBtn} onClick={() => setDocument(null)}>
                {t.backToLibrary}
              </button>
              <span className={styles.docTitle}>{doc?.title ?? ""}</span>
            </>
          )}
        </div>
        <div className={styles.right}>
          <div className={styles.langSwitch} role="group" aria-label="Language">
            {(["zh-TW", "en"] as Lang[]).map((l) => (
              <button
                key={l}
                className={lang === l ? styles.langActive : styles.langBtn}
                onClick={() => setLang(l)}
              >
                {l === "zh-TW" ? "中" : "EN"}
              </button>
            ))}
          </div>
          <span
            className={styles.statusDot}
            data-ok={health?.db ?? false}
            title={health === null ? t.apiOffline : `API ✓ / DB ${health.db ? "✓" : "✗"}`}
          />
        </div>
      </header>
      <main className={styles.panes}>
        <PDFPane />
        <ChatPane />
      </main>
    </div>
  );
}
