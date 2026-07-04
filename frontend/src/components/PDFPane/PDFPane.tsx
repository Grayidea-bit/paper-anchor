import styles from "./PDFPane.module.css";
import { useReaderStore } from "../../stores/readerStore";

/** M0 佔位；M1 換成 PDF.js 渲染 + 高亮層 */
export function PDFPane() {
  const documentId = useReaderStore((s) => s.documentId);

  return (
    <section className={styles.pane} aria-label="文獻閱讀器">
      <div className={styles.placeholder}>
        {documentId === null ? (
          <>
            <p className={styles.hint}>尚未載入文獻</p>
            <p className={styles.sub}>M1 將在此提供 PDF 上傳與渲染</p>
          </>
        ) : (
          <p className={styles.hint}>文獻 #{documentId}</p>
        )}
      </div>
    </section>
  );
}
