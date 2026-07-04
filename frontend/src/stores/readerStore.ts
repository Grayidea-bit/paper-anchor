import { create } from "zustand";

/** PDFPane 與 ChatPane 之間唯一的溝通管道（CLAUDE.md 規範） */
export interface HighlightTarget {
  page: number;
  /** PDF 座標系的 [x0, y0, x1, y1]，可多個區塊 */
  bboxList: [number, number, number, number][];
}

export type SelectionPreset = "explain" | "translate" | "critique" | "free";

export interface SelectionAsk {
  text: string;
  chunkId: number | null;
  preset: SelectionPreset;
}

interface ReaderState {
  documentId: number | null;
  highlight: HighlightTarget | null;
  /** PDF 選取文字後的提問請求；ChatPane 消費後呼叫 clearSelectionAsk */
  selectionAsk: SelectionAsk | null;
  setDocument: (id: number | null) => void;
  jumpTo: (target: HighlightTarget) => void;
  clearHighlight: () => void;
  requestSelectionAsk: (req: SelectionAsk) => void;
  clearSelectionAsk: () => void;
}

export const useReaderStore = create<ReaderState>((set) => ({
  documentId: null,
  highlight: null,
  selectionAsk: null,
  setDocument: (id) => set({ documentId: id, highlight: null, selectionAsk: null }),
  jumpTo: (target) => set({ highlight: target }),
  clearHighlight: () => set({ highlight: null }),
  requestSelectionAsk: (req) => set({ selectionAsk: req }),
  clearSelectionAsk: () => set({ selectionAsk: null }),
}));
