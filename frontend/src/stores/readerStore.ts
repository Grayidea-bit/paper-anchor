import { create } from "zustand";

/** PDFPane 與 ChatPane 之間唯一的溝通管道（CLAUDE.md 規範） */
export interface HighlightTarget {
  page: number;
  /** PDF 座標系的 [x0, y0, x1, y1]，可多個區塊 */
  bboxList: [number, number, number, number][];
}

interface ReaderState {
  documentId: number | null;
  highlight: HighlightTarget | null;
  setDocument: (id: number | null) => void;
  jumpTo: (target: HighlightTarget) => void;
  clearHighlight: () => void;
}

export const useReaderStore = create<ReaderState>((set) => ({
  documentId: null,
  highlight: null,
  setDocument: (id) => set({ documentId: id, highlight: null }),
  jumpTo: (target) => set({ highlight: target }),
  clearHighlight: () => set({ highlight: null }),
}));
