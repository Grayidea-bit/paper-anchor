import { create } from "zustand";
import * as api from "../api/client";

export interface AnnotationState {
  documentId: number | null;
  annotations: api.Annotation[];
  loading: boolean;
  load: (documentId: number | null) => Promise<void>;
  create: (input: api.AnnotationCreate) => Promise<void>;
  updateNote: (id: number, noteText: string) => Promise<void>;
  setColor: (id: number, color: api.AnnotationColor) => Promise<void>;
  remove: (id: number) => Promise<void>;
}

export const useAnnotationStore = create<AnnotationState>((set, get) => ({
  documentId: null,
  annotations: [],
  loading: false,

  load: async (documentId) => {
    set({ documentId, annotations: [], loading: true });
    if (documentId === null) {
      set({ loading: false });
      return;
    }
    try {
      const annotations = await api.listAnnotations(documentId);
      // 使用者可能在等待期間切換文獻——stale 回應直接丟棄
      if (get().documentId !== documentId) return;
      // 排序：page 升冪、created_at 升冪
      const sorted = [...annotations].sort((a, b) => {
        if (a.page !== b.page) return a.page - b.page;
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      });
      set({ annotations: sorted, loading: false });
    } catch (err) {
      console.error("Failed to load annotations:", err);
      if (get().documentId !== documentId) return;
      set({ annotations: [], loading: false });
    }
  },

  create: async (input) => {
    const documentId = get().documentId;
    if (documentId === null) {
      console.error("Cannot create annotation: no documentId set");
      return;
    }
    try {
      const annotation = await api.createAnnotation(documentId, input);
      if (get().documentId !== documentId) return;
      // append 基於 resolve 當下列表（併發建立不互相覆蓋）且保持排序
      const updated = [...get().annotations, annotation].sort((a, b) => {
        if (a.page !== b.page) return a.page - b.page;
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      });
      set({ annotations: updated });
    } catch (err) {
      console.error("Failed to create annotation:", err);
    }
  },

  updateNote: async (id, noteText) => {
    try {
      const updated = await api.updateAnnotation(id, { note_text: noteText });
      const annotations = get().annotations.map((a) => (a.id === id ? updated : a));
      set({ annotations });
    } catch (err) {
      console.error("Failed to update annotation note:", err);
    }
  },

  setColor: async (id, color) => {
    try {
      const updated = await api.updateAnnotation(id, { color });
      const annotations = get().annotations.map((a) => (a.id === id ? updated : a));
      set({ annotations });
    } catch (err) {
      console.error("Failed to update annotation color:", err);
    }
  },

  remove: async (id) => {
    try {
      await api.deleteAnnotation(id);
      const annotations = get().annotations.filter((a) => a.id !== id);
      set({ annotations });
    } catch (err) {
      console.error("Failed to delete annotation:", err);
    }
  },
}));
