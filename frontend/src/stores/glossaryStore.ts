import { create } from "zustand";
import * as api from "../api/client";

export interface GlossaryState {
  documentId: number | null;
  entries: api.GlossaryEntry[];
  loading: boolean;
  creating: boolean;
  load: (documentId: number | null) => Promise<void>;
  create: (input: api.GlossaryCreate) => Promise<void>;
  retranslate: (id: number) => Promise<void>;
  remove: (id: number) => Promise<void>;
}

export const useGlossaryStore = create<GlossaryState>((set, get) => ({
  documentId: null,
  entries: [],
  loading: false,
  creating: false,

  load: async (documentId) => {
    set({ documentId, entries: [], loading: true });
    if (documentId === null) {
      set({ loading: false });
      return;
    }
    try {
      const entries = await api.listGlossary(documentId);
      // 排序：page 升冪、created_at 升冪
      const sorted = [...entries].sort((a, b) => {
        if (a.page !== b.page) return a.page - b.page;
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      });
      set({ entries: sorted, loading: false });
    } catch (err) {
      console.error("Failed to load glossary:", err);
      set({ entries: [], loading: false });
    }
  },

  create: async (input) => {
    const state = get();
    if (state.documentId === null) {
      console.error("Cannot create glossary entry: no documentId set");
      return;
    }
    set({ creating: true });
    try {
      const entry = await api.createGlossaryEntry(state.documentId, input);
      // append 且保持排序
      const updated = [...state.entries, entry].sort((a, b) => {
        if (a.page !== b.page) return a.page - b.page;
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      });
      set({ entries: updated, creating: false });
    } catch (err) {
      console.error("Failed to create glossary entry:", err);
      set({ creating: false });
    }
  },

  retranslate: async (id) => {
    try {
      const updated = await api.retranslateGlossaryEntry(id);
      const entries = get().entries.map((e) => (e.id === id ? updated : e));
      set({ entries });
    } catch (err) {
      console.error("Failed to retranslate glossary entry:", err);
    }
  },

  remove: async (id) => {
    try {
      await api.deleteGlossaryEntry(id);
      const entries = get().entries.filter((e) => e.id !== id);
      set({ entries });
    } catch (err) {
      console.error("Failed to delete glossary entry:", err);
    }
  },
}));
