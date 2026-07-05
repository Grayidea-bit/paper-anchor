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
  /** 圈選當下自動附掛（非使用者點擊選單「問 AI」）：消費端不得搶走輸入框焦點 */
  auto?: boolean;
  /** preset==="translate" 時附掛的原文錨點：讓 ChatPane 能在回答後提供「加入翻譯表」 */
  anchor?: { page: number; bboxList: HighlightTarget["bboxList"] };
}

/** 對話範圍（與 viewer 顯示哪篇文獻解耦，跨文獻引用跳轉時對話不中斷） */
export type ChatContext =
  | { kind: "document"; documentId: number }
  | { kind: "project"; projectId: number; name: string }
  | { kind: "library" };

interface ReaderState {
  /** viewer 目前顯示的文獻（null = 文獻庫畫面） */
  documentId: number | null;
  /** 對話面板的範圍 */
  chatContext: ChatContext | null;
  highlight: HighlightTarget | null;
  /** 跨文獻引用跳轉：等目標 PDF 載入完成後由 PDFPane 消費 */
  pendingJump: { documentId: number; target: HighlightTarget } | null;
  selectionAsk: SelectionAsk | null;
  openDocument: (id: number | null) => void;
  openProjectChat: (projectId: number, name: string) => void;
  openLibraryChat: () => void;
  /** 離開專案/全庫對話：回到目前文獻的對話（或文獻庫空狀態） */
  closeScopedChat: () => void;
  jumpTo: (target: HighlightTarget) => void;
  /** 跨文獻引用：切換 viewer 文獻但保留 chatContext；跳轉在 PDF 載入後套用 */
  jumpToDocument: (documentId: number, target: HighlightTarget) => void;
  consumePendingJump: () => void;
  clearHighlight: () => void;
  requestSelectionAsk: (req: SelectionAsk) => void;
  clearSelectionAsk: () => void;
}

export const useReaderStore = create<ReaderState>((set, get) => ({
  documentId: null,
  chatContext: null,
  highlight: null,
  pendingJump: null,
  selectionAsk: null,
  openDocument: (id) =>
    set({
      documentId: id,
      chatContext: id === null ? null : { kind: "document", documentId: id },
      highlight: null,
      pendingJump: null,
      selectionAsk: null,
    }),
  openProjectChat: (projectId, name) =>
    set({ chatContext: { kind: "project", projectId, name } }),
  openLibraryChat: () => set({ chatContext: { kind: "library" } }),
  closeScopedChat: () => {
    const docId = get().documentId;
    set({
      chatContext: docId === null ? null : { kind: "document", documentId: docId },
    });
  },
  jumpTo: (target) => set({ highlight: target }),
  jumpToDocument: (documentId, target) => {
    if (get().documentId === documentId) {
      set({ highlight: target });
      return;
    }
    // 原子更新：換文獻 + 記下待套用的跳轉；不動 chatContext
    set({ documentId, highlight: null, pendingJump: { documentId, target } });
  },
  consumePendingJump: () => {
    const pending = get().pendingJump;
    if (pending && pending.documentId === get().documentId) {
      set({ highlight: pending.target, pendingJump: null });
    }
  },
  clearHighlight: () => set({ highlight: null }),
  requestSelectionAsk: (req) => set({ selectionAsk: req }),
  clearSelectionAsk: () => set({ selectionAsk: null }),
}));
