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
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const [health, setHealth] = useState<Health | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const documentId = useReaderStore((s) => s.documentId);
  const openDocument = useReaderStore((s) => s.openDocument);

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

  const online = health?.db ?? false;

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <span className={styles.wordmark}>
          Paper&nbsp;Anchor<span className={styles.wordmarkDot}>.</span>
        </span>
        {documentId !== null && (
          <>
            <span className={styles.vRule} />
            <button className={styles.backBtn} onClick={() => openDocument(null)}>
              {t.backToLibrary}
            </button>
            <span className={styles.docTitle}>{doc?.title ?? ""}</span>
          </>
        )}
        <span className={styles.spacer} />
        <button
          className={styles.themeBtn}
          title={t.themeToggle}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
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
        <span className={styles.status}>
          <span className={styles.statusDot} data-ok={online} />
          {health === null ? t.offline : online ? t.connected : t.offline}
        </span>
      </header>
      <main className={styles.panes}>
        <PDFPane />
        <ChatPane />
      </main>
    </div>
  );
}
