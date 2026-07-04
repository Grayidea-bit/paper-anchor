import { useCallback, useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import { TextLayer } from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import styles from "./PDFPane.module.css";
import { documentFileUrl, getChunks, type Chunk } from "../../api/client";
import {
  useReaderStore,
  type HighlightTarget,
  type SelectionPreset,
} from "../../stores/readerStore";
import { Library } from "../Library/Library";
import { useT } from "../../i18n";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

/** 渲染寬度基準：頁面實際縮放 = PAGE_WIDTH / 頁寬(pt) */
const PAGE_WIDTH = 780;
const MIN_SELECTION_CHARS = 8;

interface SelMenu {
  x: number;
  y: number;
  text: string;
  page: number | null;
}

export function PDFPane() {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);
  const highlight = useReaderStore((s) => s.highlight);
  const requestSelectionAsk = useReaderStore((s) => s.requestSelectionAsk);
  const consumePendingJump = useReaderStore((s) => s.consumePendingJump);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState<SelMenu | null>(null);
  const chunksRef = useRef<Chunk[] | null>(null);

  useEffect(() => {
    if (documentId === null) {
      setPdf(null);
      return;
    }
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    setError(null);
    chunksRef.current = null;
    pdfjs
      .getDocument(documentFileUrl(documentId))
      .promise.then((p) => {
        loaded = p;
        if (!cancelled) {
          setPdf(p);
          // 跨文獻引用跳轉：PDF 就緒後套用（目標頁會被強制渲染）
          consumePendingJump();
        }
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
      setPdf(null);
      void loaded?.destroy();
    };
  }, [documentId]);

  /** 選取文字對回 chunk：同頁 + 去空白後內容包含選取開頭 */
  const resolveChunkId = useCallback(
    async (text: string, page: number | null): Promise<number | null> => {
      if (documentId === null) return null;
      try {
        chunksRef.current ??= await getChunks(documentId);
      } catch {
        return null;
      }
      const probe = text.replace(/\s+/g, "").slice(0, 40);
      if (!probe) return null;
      const pool = chunksRef.current.filter((c) => page === null || c.page === page);
      const hit = pool.find((c) => c.content.replace(/\s+/g, "").includes(probe));
      return hit?.id ?? null;
    },
    [documentId],
  );

  const onMouseUp = useCallback(() => {
    // 等瀏覽器把 selection 定案再讀
    requestAnimationFrame(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        setMenu(null);
        return;
      }
      const text = sel.toString().trim();
      if (text.length < MIN_SELECTION_CHARS) {
        setMenu(null);
        return;
      }
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      const start =
        range.startContainer instanceof Element
          ? range.startContainer
          : range.startContainer.parentElement;
      const holder = start?.closest("[data-page]") as HTMLElement | null;
      if (!holder) {
        setMenu(null);
        return;
      }
      setMenu({
        x: Math.min(Math.max(rect.left + rect.width / 2, 120), window.innerWidth - 200),
        y: Math.max(rect.top - 44, 60),
        text,
        page: Number(holder.dataset.page) || null,
      });
    });
  }, []);

  const onAction = useCallback(
    async (preset: SelectionPreset) => {
      if (!menu) return;
      const chunkId = await resolveChunkId(menu.text, menu.page);
      requestSelectionAsk({ text: menu.text.slice(0, 3000), chunkId, preset });
      setMenu(null);
      window.getSelection()?.removeAllRanges();
    },
    [menu, resolveChunkId, requestSelectionAsk],
  );

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
    <section className={styles.pane} aria-label="文獻閱讀器" onMouseUp={onMouseUp}>
      {error && <p className={styles.error}>{t.pdfError}{error}</p>}
      {!pdf && !error && <p className={styles.loading}>{t.pdfLoading}</p>}
      <div className={styles.pages}>
        {pdf &&
          Array.from({ length: pdf.numPages }, (_, i) => i + 1).map((n) => (
            <PageCanvas key={n} pdf={pdf} pageNumber={n} highlight={highlight} />
          ))}
      </div>
      {menu && (
        <div
          className={styles.selMenu}
          style={{ left: menu.x, top: menu.y }}
          onMouseUp={(e) => e.stopPropagation()}
        >
          <button onClick={() => void onAction("explain")}>{t.selExplain}</button>
          <button onClick={() => void onAction("translate")}>{t.selTranslate}</button>
          <button onClick={() => void onAction("critique")}>{t.selCritique}</button>
          <button onClick={() => void onAction("free")}>{t.selAsk}</button>
        </div>
      )}
    </section>
  );
}

/** 預設佔位高度（US Letter 比例），首頁渲染後以實際高度取代 */
const ESTIMATED_PAGE_HEIGHT = Math.round(PAGE_WIDTH * 1.294);

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
  const textRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState<number | null>(null);
  const active = highlight?.page === pageNumber;

  // 頁面虛擬化：進入可視範圍（±~1.5 頁）才渲染；引用跳轉的目標頁立即渲染
  const [visible, setVisible] = useState(pageNumber <= 3);
  useEffect(() => {
    if (visible) return;
    const holder = holderRef.current;
    if (!holder) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { root: holder.closest("section"), rootMargin: "1600px 0px" },
    );
    observer.observe(holder);
    return () => observer.disconnect();
  }, [visible]);
  useEffect(() => {
    if (active) setVisible(true);
  }, [active]);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    // StrictMode 會雙跑 effect：cleanup 需 cancel() 進行中的 renderTask，
    // 否則兩輪 render 撞同一個 canvas 會被 pdf.js 全數取消（頁面空白、scale 不設）
    let renderTask: ReturnType<PDFPageProxy["render"]> | null = null;
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
      renderTask = page.render({
        canvasContext: canvas.getContext("2d")!,
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      });
      await renderTask.promise;
      if (!cancelled) setScale(pageScale);
    })().catch((e: unknown) => {
      // 取消是預期行為；其他錯誤留下線索但不擋整份文件
      if ((e as Error)?.name !== "RenderingCancelledException") {
        console.warn(`[PDFPane] page ${pageNumber} render failed:`, e);
      }
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, pageNumber, visible]);

  // 文字層（選取提問的基礎）：獨立 effect，等 canvas 完成（scale 就緒）才渲染，
  // 避免與 canvas 渲染在同一 effect 內因 StrictMode 雙跑互相清空
  useEffect(() => {
    if (scale === null) return;
    const textDiv = textRef.current;
    if (!textDiv) return;
    let cancelled = false;
    (async () => {
      const page = await pdf.getPage(pageNumber);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      textDiv.innerHTML = "";
      textDiv.style.setProperty("--scale-factor", String(scale));
      await new TextLayer({
        textContentSource: page.streamTextContent(),
        container: textDiv,
        viewport,
      }).render();
    })().catch((e: unknown) => {
      console.warn(`[PDFPane] page ${pageNumber} text layer failed:`, e);
    });
    return () => {
      cancelled = true;
    };
  }, [pdf, pageNumber, scale]);

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
    // 長距離跳轉（如跨文獻落點）用即時捲動：smooth 動畫會被
    // 尚在進行的 canvas 渲染取消，落在半路
    const behavior = Math.abs(top - scroller.scrollTop) > 2500 ? "auto" : "smooth";
    requestAnimationFrame(() => scroller.scrollTo({ top, behavior }));
    // deps 含 scale：本頁渲染完成（高度就緒）後重新校正捲動位置
  }, [active, highlight, scale]);

  return (
    <div
      className={styles.page}
      ref={holderRef}
      data-page={pageNumber}
      style={
        scale === null
          ? { width: PAGE_WIDTH, height: ESTIMATED_PAGE_HEIGHT }
          : undefined
      }
    >
      <canvas ref={canvasRef} />
      <div className={styles.textLayer} ref={textRef} />
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
