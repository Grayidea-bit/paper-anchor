import { useEffect, useState } from "react";
import styles from "./App.module.css";
import { PDFPane } from "./components/PDFPane/PDFPane";
import { RightPane } from "./components/RightPane/RightPane";
import { SettingsModal } from "./components/Settings/SettingsModal";
import { getDocument, getHealth, type Doc, type Health } from "./api/client";
import { useReaderStore } from "./stores/readerStore";
import { useT } from "./i18n";

export default function App() {
  const t = useT();
  const [health, setHealth] = useState<Health | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
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
          className={styles.gearBtn}
          title={t.settings}
          onClick={() => setSettingsOpen(true)}
        >
          ⚙
        </button>
        <span className={styles.status}>
          <span className={styles.statusDot} data-ok={online} />
          {health === null ? t.offline : online ? t.connected : t.offline}
        </span>
      </header>
      <main className={styles.panes}>
        <PDFPane key={documentId} />
        <RightPane />
      </main>
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
