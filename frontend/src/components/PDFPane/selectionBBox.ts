import type { BBox } from "../../api/client";

/**
 * DOM selection → PDF 座標的換算結果。
 * 與 citation highlight 共用同一座標語言：PyMuPDF pt（頂左原點）。
 */
export interface SelectionAnchor {
  page: number;
  bboxList: BBox[];
}

/** 清洗參數 */
const MIN_RECT_HEIGHT = 2; // px：過濾零高度雜訊 rect
const MIN_RECT_WIDTH = 1; // px：過濾零寬度雜訊 rect
const SAME_LINE_OVERLAP = 0.6; // 垂直重疊 >60% 視為同行重複
const MAX_BBOXES = 40; // 合併後上限

/** pt 座標的中間表示（頂左原點，尚未 clamp/合併） */
interface RectPt {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * 從選取的起始節點往上找最近的 `[data-page]` holder。
 * holder 由 PageCanvas 掛上 `data-page` 與 `data-scale`。
 */
function findHolder(node: Node): HTMLElement | null {
  const start = node instanceof Element ? node : node.parentElement;
  return (start?.closest("[data-page]") as HTMLElement | null) ?? null;
}

/** rect 中心點是否落在 holder 矩形內（跨頁選取 v1 只取起始頁） */
function centerInside(rect: DOMRect, holderRect: DOMRect): boolean {
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  return (
    cx >= holderRect.left &&
    cx <= holderRect.right &&
    cy >= holderRect.top &&
    cy <= holderRect.bottom
  );
}

/** 兩個 pt rect 的垂直重疊比例（相對於較矮者的高度） */
function verticalOverlapRatio(a: RectPt, b: RectPt): number {
  const top = Math.max(a.y0, b.y0);
  const bottom = Math.min(a.y1, b.y1);
  const overlap = bottom - top;
  if (overlap <= 0) return 0;
  const minHeight = Math.min(a.y1 - a.y0, b.y1 - b.y0);
  return minHeight > 0 ? overlap / minHeight : 0;
}

/**
 * 將 DOM Range 換算為 PDF pt 座標的標註錨點。
 *
 * 座標無關 zoom 的原因：holder 的 `data-scale` 是 render 定案值
 * （= renderWidth / 頁寬pt），(rect.left − holderRect.left) 為當前 zoom 下的
 * 螢幕 px 偏移，除以同一個 scale 即回到 pt。zoom 改變時 scale 與螢幕偏移同步縮放，
 * 商恆為 pt，與 citation highlight 的 `bbox × scale` 完全互逆。
 */
export function rangeToBBoxList(range: Range): SelectionAnchor | null {
  const holder = findHolder(range.startContainer);
  if (!holder) return null;

  const scale = Number(holder.dataset.scale);
  if (!Number.isFinite(scale) || scale <= 0) return null;

  const page = Number(holder.dataset.page);
  if (!Number.isInteger(page) || page <= 0) return null;

  const holderRect = holder.getBoundingClientRect();
  // 頁面尺寸（pt）：holder 螢幕尺寸 ÷ scale，用於 clamp
  const pageW = holderRect.width / scale;
  const pageH = holderRect.height / scale;

  // 1) 逐行 rects → 只保留中心落在起始頁的 → 換算 pt + 清洗雜訊
  const rects: RectPt[] = [];
  for (const rect of Array.from(range.getClientRects())) {
    if (rect.height < MIN_RECT_HEIGHT || rect.width < MIN_RECT_WIDTH) continue;
    if (!centerInside(rect, holderRect)) continue;

    let x0 = (rect.left - holderRect.left) / scale;
    let y0 = (rect.top - holderRect.top) / scale;
    let x1 = (rect.right - holderRect.left) / scale;
    let y1 = (rect.bottom - holderRect.top) / scale;

    // clamp 進頁面範圍（pt）
    x0 = Math.max(0, Math.min(x0, pageW));
    y0 = Math.max(0, Math.min(y0, pageH));
    x1 = Math.max(0, Math.min(x1, pageW));
    y1 = Math.max(0, Math.min(y1, pageH));

    if (x1 - x0 < MIN_RECT_WIDTH / scale || y1 - y0 < MIN_RECT_HEIGHT / scale) continue;
    rects.push({ x0, y0, x1, y1 });
  }

  if (rects.length === 0) return null;

  // 2) 合併同行重複 rect（垂直重疊 >60% 取聯集）
  //    先按 (y0, x0) 排序讓同行相鄰，逐一併入已完成清單
  rects.sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
  const merged: RectPt[] = [];
  for (const r of rects) {
    const same = merged.find((m) => verticalOverlapRatio(m, r) > SAME_LINE_OVERLAP);
    if (same) {
      same.x0 = Math.min(same.x0, r.x0);
      same.y0 = Math.min(same.y0, r.y0);
      same.x1 = Math.max(same.x1, r.x1);
      same.y1 = Math.max(same.y1, r.y1);
    } else {
      merged.push({ ...r });
    }
  }

  if (merged.length === 0) return null;

  const bboxList: BBox[] = merged
    .slice(0, MAX_BBOXES)
    .map((m) => [m.x0, m.y0, m.x1, m.y1] as BBox);

  return { page, bboxList };
}
