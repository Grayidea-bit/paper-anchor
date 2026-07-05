import { useEffect, useState } from "react";
import styles from "./RightPane.module.css";
import { ChatPane } from "../ChatPane/ChatPane";
import { NotesPane } from "../NotesPane/NotesPane";
import { GlossaryPane } from "../GlossaryPane/GlossaryPane";
import { useReaderStore } from "../../stores/readerStore";
import { useAnnotationStore } from "../../stores/annotationStore";
import { useGlossaryStore } from "../../stores/glossaryStore";
import { useT } from "../../i18n";

type RightTab = "chat" | "notes" | "glossary";

/**
 * 右欄容器：對話／筆記分頁籤。
 * 關鍵：ChatPane 常駐掛載（不可條件渲染），切換分頁只用 CSS display 隱藏，
 * 這樣 SSE 串流與捲動狀態在切到筆記再切回來時仍然存在。
 */
export function RightPane() {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);
  const annotationCount = useAnnotationStore((s) => s.annotations.length);
  const glossaryCount = useGlossaryStore((s) => s.entries.length);
  const loadGlossary = useGlossaryStore((s) => s.load);
  const [tab, setTab] = useState<RightTab>("chat");

  // 切換文獻時自動回到「對話」籤
  useEffect(() => {
    setTab("chat");
  }, [documentId]);

  // 翻譯表：documentId 變更時載入（null 清空），比照 annotationStore.load 的觸發位置
  useEffect(() => {
    void loadGlossary(documentId);
  }, [documentId, loadGlossary]);

  const notesAvailable = documentId !== null;

  return (
    <section className={styles.pane} aria-label="右欄">
      <div className={styles.tabs} role="tablist">
        <button
          type="button"
          role="tab"
          className={styles.tab}
          data-active={tab === "chat"}
          aria-selected={tab === "chat"}
          onClick={() => setTab("chat")}
        >
          {t.tabChat}
        </button>
        {notesAvailable && (
          <button
            type="button"
            role="tab"
            className={styles.tab}
            data-active={tab === "notes"}
            aria-selected={tab === "notes"}
            onClick={() => setTab("notes")}
          >
            {t.tabNotes}
            <span className={styles.tabCount}>({annotationCount})</span>
          </button>
        )}
        {notesAvailable && (
          <button
            type="button"
            role="tab"
            className={styles.tab}
            data-active={tab === "glossary"}
            aria-selected={tab === "glossary"}
            onClick={() => setTab("glossary")}
          >
            {t.tabGlossary}
            <span className={styles.tabCount}>({glossaryCount})</span>
          </button>
        )}
      </div>
      <div className={styles.body}>
        <div
          className={`${styles.tabPanel} ${tab === "chat" ? "" : styles.hidden}`}
        >
          <ChatPane />
        </div>
        {notesAvailable && (
          <div
            className={`${styles.tabPanel} ${tab === "notes" ? "" : styles.hidden}`}
          >
            <NotesPane />
          </div>
        )}
        {notesAvailable && (
          <div
            className={`${styles.tabPanel} ${tab === "glossary" ? "" : styles.hidden}`}
          >
            <GlossaryPane />
          </div>
        )}
      </div>
    </section>
  );
}
