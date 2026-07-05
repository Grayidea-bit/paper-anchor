import { useEffect, useState } from "react";
import styles from "./RightPane.module.css";
import { ChatPane } from "../ChatPane/ChatPane";
import { NotesPane } from "../NotesPane/NotesPane";
import { useReaderStore } from "../../stores/readerStore";
import { useAnnotationStore } from "../../stores/annotationStore";
import { useT } from "../../i18n";

type RightTab = "chat" | "notes";

/**
 * 右欄容器：對話／筆記分頁籤。
 * 關鍵：ChatPane 常駐掛載（不可條件渲染），切換分頁只用 CSS display 隱藏，
 * 這樣 SSE 串流與捲動狀態在切到筆記再切回來時仍然存在。
 */
export function RightPane() {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);
  const annotationCount = useAnnotationStore((s) => s.annotations.length);
  const [tab, setTab] = useState<RightTab>("chat");

  // 切換文獻時自動回到「對話」籤
  useEffect(() => {
    setTab("chat");
  }, [documentId]);

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
      </div>
    </section>
  );
}
