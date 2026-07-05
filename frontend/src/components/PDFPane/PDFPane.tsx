import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import { TextLayer } from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import styles from "./PDFPane.module.css";
import {
  documentFileUrl,
  getChunks,
  type Annotation,
  type AnnotationColor,
  type BBox,
  type Chunk,
} from "../../api/client";
import {
  useReaderStore,
  type HighlightTarget,
  type SelectionPreset,
} from "../../stores/readerStore";
import { useAnnotationStore } from "../../stores/annotationStore";
import { Library } from "../Library/Library";
import { useT } from "../../i18n";
import { rangeToBBoxList } from "./selectionBBox";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

/** 渲染寬度基準（zoom=100%）：頁面實際縮放 = renderWidth / 頁寬(pt) */
const PAGE_WIDTH = 780;
const MIN_SELECTION_CHARS = 8;
const ZOOM_MIN = 50;
const ZOOM_MAX = 200;
const ZOOM_STEP = 25;
/** 穩定的空陣列參照：無標註的頁面共用同一個，避免每次 render 產生新 [] */
const EMPTY_ANNOTATIONS: Annotation[] = [];
/** 標註工具模式：cursor=維持既有選取選單；underline/highlight=直接建立標註 */
type AnnotMode = "cursor" | "underline" | "highlight";
const ANNOT_COLORS: AnnotationColor[] = ["amber", "terracotta", "sage", "slate"];
const DEFAULT_ANNOT_COLOR: AnnotationColor = "amber";

function loadAnnotColor(): AnnotationColor {
  const saved = localStorage.getItem("annot_color");
  return (ANNOT_COLORS as string[]).includes(saved ?? "")
    ? (saved as AnnotationColor)
    : DEFAULT_ANNOT_COLOR;
}

interface SelMenu {
  x: number;
  y: number;
  text: string;
  page: number | null;
  /** 選取當下（消失前）捕捉的 bbox；undefined 表尚未支援，null 表換算失敗 */
  bboxList: BBox[] | null;
}

/** 「加註解」popover 狀態：沿用 menu 的定位與捕捉結果 */
interface NotePopover {
  x: number;
  y: number;
  text: string;
  page: number | null;
  bboxList: BBox[] | null;
}

export function PDFPane() {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);
  const highlight = useReaderStore((s) => s.highlight);
  const requestSelectionAsk = useReaderStore((s) => s.requestSelectionAsk);
  const consumePendingJump = useReaderStore((s) => s.consumePendingJump);
  const annotations = useAnnotationStore((s) => s.annotations);
  const loadAnnotations = useAnnotationStore((s) => s.load);
  const createAnnotation = useAnnotationStore((s) => s.create);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState<SelMenu | null>(null);
  const [notePopover, setNotePopover] = useState<NotePopover | null>(null);
  const [noteText, setNoteText] = useState("");
  const chunksRef = useRef<Chunk[] | null>(null);

  // 工具列模式：cursor（預設，不持久化）/ underline / highlight
  const [mode, setMode] = useState<AnnotMode>("cursor");
  // 標註顏色：持久化於 localStorage
  const [annotColor, setAnnotColor] = useState<AnnotationColor>(() => loadAnnotColor());
  const changeAnnotColor = useCallback((color: AnnotationColor) => {
    setAnnotColor(color);
    localStorage.setItem("annot_color", color);
  }, []);

  // 文獻切換/關閉：模式重置回 cursor
  useEffect(() => {
    setMode("cursor");
    setMenu(null);
    setNotePopover(null);
  }, [documentId]);

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

  // 使用者標註：documentId 變更時載入（null 清空）
  useEffect(() => {
    void loadAnnotations(documentId);
  }, [documentId, loadAnnotations]);

  // 標註按頁分組，比照 highlight 以 prop 傳入 PageCanvas
  const annotationsByPage = useMemo(() => {
    const map = new Map<number, Annotation[]>();
    for (const a of annotations) {
      const list = map.get(a.page);
      if (list) list.push(a);
      else map.set(a.page, [a]);
    }
    return map;
  }, [annotations]);

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
      const page = Number(holder.dataset.page) || null;
      // 按鈕點擊後 selection 會消失：bbox 必須在此刻（selection 定案時）捕捉
      const anchor = rangeToBBoxList(range);
      const bboxList = anchor?.bboxList ?? null;

      if (mode === "underline" || mode === "highlight") {
        if (!bboxList) return; // 換算失敗：靜默放棄，不建立
        void (async () => {
          const chunkId = await resolveChunkId(text, page);
          await createAnnotation({
            type: mode,
            color: annotColor,
            page: anchor!.page,
            bbox_list: bboxList,
            chunk_id: chunkId,
            selected_text: text.slice(0, 3000),
          });
          window.getSelection()?.removeAllRanges();
        })();
        return;
      }

      // cursor 模式：維持既有 SelMenu 行為
      setMenu({
        x: Math.min(Math.max(rect.left + rect.width / 2, 120), window.innerWidth - 200),
        y: Math.max(rect.top - 44, 60),
        text,
        page,
        bboxList,
      });
    });
  }, [mode, annotColor, resolveChunkId, createAnnotation]);

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

  /** 選單「加註解」：原位切換為 popover，帶著已捕捉的 bbox/page */
  const onOpenNote = useCallback(() => {
    if (!menu) return;
    setNotePopover({ x: menu.x, y: menu.y, text: menu.text, page: menu.page, bboxList: menu.bboxList });
    setNoteText("");
    setMenu(null);
  }, [menu]);

  const closeNotePopover = useCallback(() => {
    setNotePopover(null);
    setNoteText("");
    window.getSelection()?.removeAllRanges();
  }, []);

  const onSaveNote = useCallback(async () => {
    if (!notePopover) return;
    const trimmed = noteText.trim();
    if (!trimmed) return;
    // 後端要求 bbox_list min_length=1；換算失敗（null）時直接放棄，
    // 不送必失敗的 422 請求，也不假裝成功關閉 popover（避免使用者誤以為已存）。
    if (!notePopover.bboxList || notePopover.bboxList.length === 0) return;
    const chunkId = await resolveChunkId(notePopover.text, notePopover.page);
    await createAnnotation({
      type: "note",
      color: annotColor,
      page: notePopover.page ?? 1,
      bbox_list: notePopover.bboxList,
      chunk_id: chunkId,
      selected_text: notePopover.text.slice(0, 3000),
      note_text: trimmed,
    });
    closeNotePopover();
  }, [notePopover, noteText, annotColor, resolveChunkId, createAnnotation, closeNotePopover]);

  // PDF 獨立縮放（只影響左欄；右欄對話零影響）
  const [zoom, setZoom] = useState(() => {
    const saved = Number(localStorage.getItem("pdf_zoom"));
    return saved >= ZOOM_MIN && saved <= ZOOM_MAX ? saved : 100;
  });
  const renderWidth = Math.round((PAGE_WIDTH * zoom) / 100);
  const paneElRef = useRef<HTMLElement | null>(null);
  const changeZoom = useCallback((delta: number | "reset") => {
    setZoom((prev) => {
      const next = delta === "reset" ? 100 : prev + delta;
      const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
      if (clamped === prev) return prev;
      localStorage.setItem("pdf_zoom", String(clamped));
      // 等比捲動校正：維持視點位置
      const pane = paneElRef.current;
      if (pane) {
        const ratio = clamped / prev;
        requestAnimationFrame(() => {
          pane.scrollTop = pane.scrollTop * ratio;
        });
      }
      return clamped;
    });
  }, []);

  // 頁碼膠囊：viewport 中線落在哪一頁
  const [currentPage, setCurrentPage] = useState(1);
  const paneRef = useRef<HTMLElement | null>(null);
  const onScroll = useCallback(() => {
    const pane = paneRef.current;
    if (!pane) return;
    const mid = pane.getBoundingClientRect().top + pane.clientHeight / 2;
    let best = 1;
    for (const holder of pane.querySelectorAll<HTMLElement>("[data-page]")) {
      const r = holder.getBoundingClientRect();
      if (r.top <= mid) best = Number(holder.dataset.page) || best;
      else break;
    }
    setCurrentPage(best);
  }, []);

  if (documentId === null) {
    return (
      <section className={styles.paneLibrary} aria-label="文獻庫">
        <Library />
      </section>
    );
  }

  const numPages = pdf?.numPages ?? 0;

  return (
    <div className={styles.paneWrap}>
      <section
        className={styles.pane}
        aria-label="文獻閱讀器"
        data-mode={mode}
        onMouseUp={onMouseUp}
        onScroll={onScroll}
        ref={(el) => {
          paneRef.current = el;
          paneElRef.current = el;
        }}
      >
        {error && <p className={styles.error}>{t.pdfError}{error}</p>}
        {!pdf && !error && <p className={styles.loading}>{t.pdfLoading}</p>}
        <div className={styles.pages}>
          {pdf &&
            Array.from({ length: numPages }, (_, i) => i + 1).map((n) => (
              <PageCanvas
                key={n}
                pdf={pdf}
                pageNumber={n}
                highlight={highlight}
                annotations={annotationsByPage.get(n) ?? EMPTY_ANNOTATIONS}
                renderWidth={renderWidth}
              />
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
            <button className={styles.selMenuAsk} onClick={() => void onAction("free")}>
              {t.selAsk}…
            </button>
            <button onClick={onOpenNote} disabled={!menu.bboxList} title={menu.bboxList ? undefined : t.noteNoAnchor}>
              {t.addNote}
            </button>
            <span className={styles.selMenuArrow} />
          </div>
        )}
        {notePopover && (
          <div
            className={styles.notePopover}
            style={{ left: notePopover.x, top: notePopover.y }}
            onMouseUp={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <textarea
              className={styles.notePopoverTextarea}
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder={t.notePlaceholder}
              autoFocus
              rows={3}
            />
            {!notePopover.bboxList && (
              <p className={styles.notePopoverWarn}>{t.noteNoAnchor}</p>
            )}
            <div className={styles.notePopoverActions}>
              <button onClick={closeNotePopover}>{t.cancel}</button>
              <button
                className={styles.notePopoverSave}
                onClick={() => void onSaveNote()}
                disabled={!noteText.trim() || !notePopover.bboxList}
              >
                {t.save}
              </button>
            </div>
            <span className={styles.selMenuArrow} />
          </div>
        )}
      </section>
      {pdf && (
        <span className={styles.pagePill}>
          p. {currentPage} / {numPages}
        </span>
      )}
      {pdf && (
        <span className={styles.toolBar}>
          <button
            className={styles.modeBtn}
            aria-pressed={mode === "cursor"}
            data-active={mode === "cursor" || undefined}
            title={t.cursor}
            onClick={() => setMode("cursor")}
          >
            {t.cursor}
          </button>
          <button
            className={styles.modeBtn}
            aria-pressed={mode === "underline"}
            data-active={mode === "underline" || undefined}
            title={t.underline}
            onClick={() => setMode("underline")}
          >
            {t.underline}
          </button>
          <button
            className={styles.modeBtn}
            aria-pressed={mode === "highlight"}
            data-active={mode === "highlight" || undefined}
            title={t.highlightMode}
            onClick={() => setMode("highlight")}
          >
            {t.highlightMode}
          </button>
          <span className={styles.toolBarDivider} />
          <span className={styles.colorSwatches}>
            {ANNOT_COLORS.map((c) => (
              <button
                key={c}
                className={styles.colorSwatch}
                data-active={c === annotColor || undefined}
                style={{ background: `var(--annot-${c})` }}
                aria-label={c}
                aria-pressed={c === annotColor}
                onClick={() => changeAnnotColor(c)}
              />
            ))}
          </span>
          <span className={styles.toolBarDivider} />
          <button onClick={() => changeZoom(-ZOOM_STEP)} disabled={zoom <= ZOOM_MIN}>
            −
          </button>
          <button className={styles.zoomValue} onClick={() => changeZoom("reset")} title="100%">
            {zoom}%
          </button>
          <button onClick={() => changeZoom(ZOOM_STEP)} disabled={zoom >= ZOOM_MAX}>
            ＋
          </button>
        </span>
      )}
      {pdf && highlight && numPages > 0 && (
        <span className={styles.miniTrack}>
          <span
            className={styles.miniMark}
            style={{
              top: `${((highlight.page - 1) / numPages) * 100}%`,
              height: `${Math.max(100 / numPages, 3)}%`,
            }}
          />
        </span>
      )}
    </div>
  );
}

function PageCanvas({
  pdf,
  pageNumber,
  highlight,
  annotations,
  renderWidth,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  highlight: HighlightTarget | null;
  /** 本頁的使用者標註（已由 PDFPane 按頁分組） */
  annotations: Annotation[];
  /** 目標渲染寬度（px），隨 zoom 變動 */
  renderWidth: number;
}) {
  const t = useT();
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
      const pageScale = renderWidth / base.width;
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
  }, [pdf, pageNumber, visible, renderWidth]);

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
      // scale 是 render 定案值（renderWidth / 頁寬pt）；選取換算 (selectionBBox.ts) 讀取
      data-scale={scale ?? undefined}
      style={
        scale === null
          ? { width: renderWidth, height: Math.round(renderWidth * 1.294) }
          : undefined
      }
    >
      <canvas ref={canvasRef} />
      <div className={styles.textLayer} ref={textRef} />
      {/* 使用者標註層：靜態常駐、無動畫、pointer-events:none、z-index 低於 citation 高亮 */}
      {scale !== null && annotations.length > 0 && (
        <div className={styles.annotationLayer} aria-hidden="true">
          {annotations.map((annot) =>
            annot.bbox_list.map(([x0, y0, x1, y1], idx) => {
              const box = {
                left: x0 * scale,
                top: y0 * scale,
                width: (x1 - x0) * scale,
                height: (y1 - y0) * scale,
              };
              const colorVar = `var(--annot-${annot.color})`;
              if (annot.type === "highlight") {
                return (
                  <div
                    key={`${annot.id}-${idx}`}
                    className={styles.annotHighlight}
                    style={{ ...box, background: colorVar }}
                  />
                );
              }
              if (annot.type === "underline") {
                return (
                  <div
                    key={`${annot.id}-${idx}`}
                    className={styles.annotUnderline}
                    style={{ ...box, borderBottomColor: colorVar }}
                  />
                );
              }
              // note：虛線底線；第一個 bbox 右上角加 ✎ marker
              return (
                <div
                  key={`${annot.id}-${idx}`}
                  className={styles.annotNote}
                  style={{ ...box, borderBottomColor: colorVar }}
                >
                  {idx === 0 && (
                    <span className={styles.annotNoteMarker} style={{ color: colorVar }}>
                      ✎
                    </span>
                  )}
                </div>
              );
            }),
          )}
        </div>
      )}
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
      {active && scale !== null && highlight.bboxList[0] && (
        <span
          className={styles.anchorTag}
          style={{
            left: highlight.bboxList[0][2] * scale - 4,
            top: Math.max(highlight.bboxList[0][1] * scale - 24, 4),
          }}
        >
          §&nbsp;{t.anchorTag}
        </span>
      )}
      <span className={styles.pageNo}>{pageNumber}</span>
    </div>
  );
}
