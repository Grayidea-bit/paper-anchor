import { useMemo, useState } from "react";
import styles from "./NotesPane.module.css";
import type { Annotation } from "../../api/client";
import { useAnnotationStore } from "../../stores/annotationStore";
import { useReaderStore } from "../../stores/readerStore";
import { useT } from "../../i18n";

const TYPE_ICON: Record<Annotation["type"], string> = {
  underline: "—",
  highlight: "▉",
  note: "✎",
};

function truncate(text: string, max = 120): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max).trimEnd()}…`;
}

/** annotations（已由 store 依 page/created_at 排序）→ 依頁碼分組，維持既有順序 */
function groupByPage(annotations: Annotation[]): Array<[number, Annotation[]]> {
  const groups: Array<[number, Annotation[]]> = [];
  for (const annot of annotations) {
    const last = groups[groups.length - 1];
    if (last && last[0] === annot.page) {
      last[1].push(annot);
    } else {
      groups.push([annot.page, [annot]]);
    }
  }
  return groups;
}

export function NotesPane() {
  const t = useT();
  const annotations = useAnnotationStore((s) => s.annotations);
  const removeAnnotation = useAnnotationStore((s) => s.remove);
  const updateNote = useAnnotationStore((s) => s.updateNote);
  const jumpTo = useReaderStore((s) => s.jumpTo);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  const groups = useMemo(() => groupByPage(annotations), [annotations]);

  if (annotations.length === 0) {
    return (
      <section className={styles.pane} aria-label={t.ariaNotesPane}>
        <div className={styles.emptyWrap}>
          <p className={styles.hint}>{t.notesEmpty}</p>
        </div>
      </section>
    );
  }

  const startEdit = (annot: Annotation) => {
    setEditingId(annot.id);
    setDraft(annot.note_text ?? "");
  };
  const cancelEdit = () => {
    setEditingId(null);
    setDraft("");
  };
  const saveEdit = (id: number) => {
    updateNote(id, draft);
    setEditingId(null);
    setDraft("");
  };

  return (
    <section className={styles.pane} aria-label="筆記面板">
      {groups.map(([page, rows]) => (
        <div className={styles.group} key={page}>
          <div className={styles.groupTitle}>p. {page}</div>
          {rows.map((annot) => {
            const isEditing = editingId === annot.id;
            const hasNote = annot.note_text.trim().length > 0;
            return (
              <div
                key={annot.id}
                className={styles.row}
                onClick={() => jumpTo({ page: annot.page, bboxList: annot.bbox_list })}
              >
                <div className={styles.rowMain}>
                  <span className={styles.typeIcon}>{TYPE_ICON[annot.type]}</span>
                  <span
                    className={styles.colorDot}
                    style={
                      {
                        "--dot-color": `var(--annot-${annot.color})`,
                      } as React.CSSProperties
                    }
                  />
                  <span className={styles.selectedText}>
                    {truncate(annot.selected_text)}
                  </span>
                </div>

                {!isEditing && hasNote && (
                  <p className={styles.noteText}>{annot.note_text}</p>
                )}

                {isEditing ? (
                  <div
                    className={styles.editWrap}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <textarea
                      className={styles.editArea}
                      value={draft}
                      placeholder={t.notePlaceholder}
                      onChange={(e) => setDraft(e.target.value)}
                      autoFocus
                    />
                    <div className={styles.editActions}>
                      <button
                        type="button"
                        className={styles.cancelBtn}
                        onClick={cancelEdit}
                      >
                        {t.cancel}
                      </button>
                      <button
                        type="button"
                        className={styles.saveBtn}
                        onClick={() => saveEdit(annot.id)}
                      >
                        {t.save}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className={styles.rowActions}>
                    <button
                      type="button"
                      className={styles.actionBtn}
                      onClick={(e) => {
                        e.stopPropagation();
                        startEdit(annot);
                      }}
                    >
                      {hasNote ? t.editNote : t.addNoteToAnnotation}
                    </button>
                    <button
                      type="button"
                      className={`${styles.actionBtn} ${styles.deleteBtn}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        removeAnnotation(annot.id);
                      }}
                    >
                      {t.deleteAnnotation}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}
