import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import styles from "./PDFPane.module.css";
import { documentFileUrl } from "../../api/client";
import { useReaderStore, type HighlightTarget } from "../../stores/readerStore";
import { Library } from "../Library/Library";
import { useT } from "../../i18n";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

/** 渲染寬度基準：頁面實際縮放 = PAGE_WIDTH / 頁寬(pt) */
const PAGE_WIDTH = 780;

export function PDFPane() {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);
  const highlight = useReaderStore((s) => s.highlight);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (documentId === null) {
      setPdf(null);
      return;
    }
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    setError(null);
    pdfjs
      .getDocument(documentFileUrl(documentId))
      .promise.then((p) => {
        loaded = p;
        if (!cancelled) setPdf(p);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
      setPdf(null);
      void loaded?.destroy();
    };
  }, [documentId]);

  if (documentId === null) {
    return (
      <section className={styles.pane} aria-label="文獻庫">
        <div className={styles.centerWrap}>
          <Library />
        </div>
      </section>
    );
  }

  return (
    <section className={styles.pane} aria-label="文獻閱讀器">
      {error && <p className={styles.error}>{t.pdfError}{error}</p>}
      {!pdf && !error && <p className={styles.loading}>{t.pdfLoading}</p>}
      <div className={styles.pages}>
        {pdf &&
          Array.from({ length: pdf.numPages }, (_, i) => i + 1).map((n) => (
            <PageCanvas key={n} pdf={pdf} pageNumber={n} highlight={highlight} />
          ))}
      </div>
    </section>
  );
}

function PageCanvas({
  pdf,
  pageNumber,
  highlight,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  highlight: HighlightTarget | null;
}) {
  const holderRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState<number | null>(null);
  const active = highlight?.page === pageNumber;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const page = await pdf.getPage(pageNumber);
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      const pageScale = PAGE_WIDTH / base.width;
      const viewport = page.getViewport({ scale: pageScale });
      const canvas = canvasRef.current;
      const holder = holderRef.current;
      if (!canvas || !holder) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = viewport.width * dpr;
      canvas.height = viewport.height * dpr;
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      holder.style.width = `${viewport.width}px`;
      holder.style.height = `${viewport.height}px`;
      await page.render({
        canvasContext: canvas.getContext("2d")!,
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      }).promise;
      if (!cancelled) setScale(pageScale);
    })().catch(() => {
      /* 單頁渲染失敗不擋整份文件 */
    });
    return () => {
      cancelled = true;
    };
  }, [pdf, pageNumber]);

  // 跳頁：引用錨點的前端終點。
  // 不用 scrollIntoView(smooth)：同幀插入高亮 DOM 會讓 Chrome 取消該動畫，
  // 改成對捲動容器顯式 scrollTo。
  useEffect(() => {
    const holder = holderRef.current;
    if (!active || !holder) return;
    const scroller = holder.closest("section");
    if (!scroller) return;
    const top =
      holder.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top +
      scroller.scrollTop -
      24;
    requestAnimationFrame(() => scroller.scrollTo({ top, behavior: "smooth" }));
    // deps 含 scale：本頁渲染完成（高度就緒）後重新校正捲動位置
  }, [active, highlight, scale]);

  return (
    <div className={styles.page} ref={holderRef}>
      <canvas ref={canvasRef} />
      {/* 高亮層：PyMuPDF bbox 為頂左原點 pt 座標，乘 scale 即 CSS px */}
      {active &&
        scale !== null &&
        highlight.bboxList.map(([x0, y0, x1, y1], idx) => (
          <div
            key={idx}
            className={styles.highlight}
            style={{
              left: x0 * scale,
              top: y0 * scale,
              width: (x1 - x0) * scale,
              height: (y1 - y0) * scale,
            }}
          />
        ))}
      <span className={styles.pageNo}>{pageNumber}</span>
    </div>
  );
}
