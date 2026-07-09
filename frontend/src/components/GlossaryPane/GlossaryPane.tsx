import { useEffect, useState } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import styles from "./GlossaryPane.module.css";
import { useGlossaryStore } from "../../stores/glossaryStore";
import { useReaderStore } from "../../stores/readerStore";
import { useT } from "../../i18n";
import { SimpleMarkdown } from "../ChatPane/ChatPane";
import type { GlossaryEntry } from "../../api/client";

export function GlossaryPane() {
  const t = useT();
  const entries = useGlossaryStore((s) => s.entries);
  const creating = useGlossaryStore((s) => s.creating);
  const retranslate = useGlossaryStore((s) => s.retranslate);
  const remove = useGlossaryStore((s) => s.remove);
  const jumpTo = useReaderStore((s) => s.jumpTo);
  const [openId, setOpenId] = useState<number | null>(null);

  const openEntry = entries.find((e) => e.id === openId) ?? null;

  if (entries.length === 0 && !creating) {
    return (
      <section className={styles.pane} aria-label={t.ariaGlossaryPane}>
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
              onClick={() => setOpenId(entry.id)}
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
                  <span className={styles.translation}>{entry.translation}</span>
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
      {openEntry && (
        <GlossaryEntryModal
          entry={openEntry}
          onClose={() => setOpenId(null)}
          onJump={() => {
            jumpTo({ page: openEntry.page, bboxList: openEntry.bbox_list });
            setOpenId(null);
          }}
          onRetranslate={() => void retranslate(openEntry.id)}
          onDelete={() => {
            void remove(openEntry.id);
            setOpenId(null);
          }}
        />
      )}
    </section>
  );
}

function GlossaryEntryModal({
  entry,
  onClose,
  onJump,
  onRetranslate,
  onDelete,
}: {
  entry: GlossaryEntry;
  onClose: () => void;
  onJump: () => void;
  onRetranslate: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const failed = entry.translation.trim().length === 0;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className={styles.overlay}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className={styles.modal} role="dialog" aria-label={entry.term}>
        <div className={styles.modalHeader}>
          <span className={styles.modalPageTag}>p.{entry.page}</span>
          <span className={styles.modalTitle}>{entry.term}</span>
          <button className={styles.modalClose} onClick={onClose} title={t.close}>
            ✕
          </button>
        </div>
        <div className={styles.modalBody}>
          {failed ? (
            <p className={styles.modalFailedText}>{t.translationFailed}</p>
          ) : (
            <p className={styles.modalTranslation}>{entry.translation}</p>
          )}
          {entry.notes.trim() && (
            <div className={styles.modalNotes}>
              <SimpleMarkdown content={entry.notes} />
            </div>
          )}
        </div>
        <div className={styles.modalActions}>
          <button className={styles.modalActionBtn} onClick={onJump}>
            {t.jumpToSource}
          </button>
          {failed && (
            <button className={styles.modalActionBtn} onClick={onRetranslate}>
              <RotateCcw size={13} strokeWidth={2} />
              {t.retranslate}
            </button>
          )}
          <button className={styles.modalActionBtnDanger} onClick={onDelete}>
            <Trash2 size={13} strokeWidth={2} />
            {t.deleteEntry}
          </button>
        </div>
      </div>
    </div>
  );
}
