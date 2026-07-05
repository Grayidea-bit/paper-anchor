import { RotateCcw, Trash2 } from "lucide-react";
import styles from "./GlossaryPane.module.css";
import { useGlossaryStore } from "../../stores/glossaryStore";
import { useReaderStore } from "../../stores/readerStore";
import { useT } from "../../i18n";

export function GlossaryPane() {
  const t = useT();
  const entries = useGlossaryStore((s) => s.entries);
  const creating = useGlossaryStore((s) => s.creating);
  const retranslate = useGlossaryStore((s) => s.retranslate);
  const remove = useGlossaryStore((s) => s.remove);
  const jumpTo = useReaderStore((s) => s.jumpTo);

  if (entries.length === 0 && !creating) {
    return (
      <section className={styles.pane} aria-label="翻譯表面板">
        <div className={styles.emptyWrap}>
          <p className={styles.hint}>{t.glossaryEmpty}</p>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.pane} aria-label="翻譯表面板">
      <div className={styles.list}>
        {entries.map((entry) => {
          const failed = entry.translation.trim().length === 0;
          return (
            <div
              key={entry.id}
              className={styles.row}
              onClick={() => jumpTo({ page: entry.page, bboxList: entry.bbox_list })}
            >
              <span className={styles.pageTag}>p.{entry.page}</span>
              <div className={styles.rowBody}>
                <span className={styles.term}>{entry.term}</span>
                {failed ? (
                  <div className={styles.failedWrap}>
                    <span className={styles.failedText}>{t.translationFailed}</span>
                    <button
                      type="button"
                      className={styles.retryBtn}
                      onClick={(e) => {
                        e.stopPropagation();
                        void retranslate(entry.id);
                      }}
                      title={t.retranslate}
                    >
                      <RotateCcw size={12} strokeWidth={2} />
                      {t.retranslate}
                    </button>
                  </div>
                ) : (
                  <>
                    <span className={styles.translation}>{entry.translation}</span>
                    {entry.notes.trim() && (
                      <span className={styles.notes}>{entry.notes}</span>
                    )}
                  </>
                )}
              </div>
              <button
                type="button"
                className={styles.deleteBtn}
                onClick={(e) => {
                  e.stopPropagation();
                  void remove(entry.id);
                }}
                title={t.deleteEntry}
              >
                <Trash2 size={13} strokeWidth={2} />
              </button>
            </div>
          );
        })}
        {creating && (
          <div className={`${styles.row} ${styles.rowSkeleton}`}>
            <span className={styles.pageTag}>···</span>
            <div className={styles.rowBody}>
              <span className={styles.translating}>{t.translating}</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
