import styles from "./ChatPane.module.css";

/** M0 佔位；M2 實作 SSE 對話與引用渲染 */
export function ChatPane() {
  return (
    <section className={styles.pane} aria-label="對話面板">
      <div className={styles.messages}>
        <p className={styles.hint}>上傳文獻後，可在此與 LLM 討論內容</p>
      </div>
      <div className={styles.inputRow}>
        <textarea
          className={styles.input}
          placeholder="M2 開放提問…"
          rows={2}
          disabled
        />
        <button className={styles.send} disabled>
          送出
        </button>
      </div>
    </section>
  );
}
