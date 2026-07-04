import { create } from "zustand";

export type Lang = "zh-TW" | "en";

const DICT = {
  "zh-TW": {
    appName: "AI 文獻導讀",
    backToLibrary: "← 文獻庫",
    apiOffline: "API 未連線",
    upload: "上傳 PDF 文獻",
    uploading: "上傳中…",
    emptyLibrary: "尚無文獻，先上傳一篇吧",
    pages: "頁",
    delete: "刪除",
    status_uploaded: "已上傳",
    status_parsing: "解析中…",
    status_embedding: "建立索引…",
    status_digesting: "產生導讀…",
    status_ready: "可閱讀",
    status_failed: "失敗",
    pdfLoading: "載入中…",
    pdfError: "PDF 載入失敗：",
    chatEmptyHint: "上傳文獻後，可在此與 LLM 討論內容",
    digest: "導讀",
    digestPending: "導讀產生中…",
    regenerate: "重新產生",
    inputPlaceholder: "就這篇文獻提問…（Enter 送出，Shift+Enter 換行）",
    send: "送出",
    newConversation: "開新對話",
    jumpToSource: "跳到原文",
    citationPending: "引用解析中…",
    jumpToPage: (p: number) => `跳到第 ${p} 頁`,
    answerLanguage: "回答語言",
    selExplain: "解釋",
    selTranslate: "翻譯",
    selCritique: "質疑",
    selAsk: "提問",
    presetExplain: "請解釋這段內容的意思與它在本文中的角色。",
    presetTranslate: "請把這段翻譯成繁體中文，並用白話補充說明。",
    presetCritique: "這段的論證、方法或假設有什麼可質疑之處？",
    selectedPassage: "已選取原文",
    dismiss: "移除",
  },
  en: {
    appName: "Paper Reader",
    backToLibrary: "← Library",
    apiOffline: "API offline",
    upload: "Upload PDF",
    uploading: "Uploading…",
    emptyLibrary: "No papers yet — upload one to begin",
    pages: "pages",
    delete: "Delete",
    status_uploaded: "Uploaded",
    status_parsing: "Parsing…",
    status_embedding: "Indexing…",
    status_digesting: "Digesting…",
    status_ready: "Ready",
    status_failed: "Failed",
    pdfLoading: "Loading…",
    pdfError: "Failed to load PDF: ",
    chatEmptyHint: "Upload a paper to start discussing it with the LLM",
    digest: "DIGEST",
    digestPending: "Generating digest…",
    regenerate: "Regenerate",
    inputPlaceholder: "Ask about this paper… (Enter to send, Shift+Enter for newline)",
    send: "Send",
    newConversation: "New conversation",
    jumpToSource: "Jump to source",
    citationPending: "Resolving citation…",
    jumpToPage: (p: number) => `Jump to page ${p}`,
    answerLanguage: "Answer language",
    selExplain: "Explain",
    selTranslate: "Translate",
    selCritique: "Challenge",
    selAsk: "Ask",
    presetExplain: "Explain this passage and its role in the paper.",
    presetTranslate: "Translate this passage into English and paraphrase it plainly.",
    presetCritique: "What in this passage's argument, method, or assumptions could be challenged?",
    selectedPassage: "Selected passage",
    dismiss: "Dismiss",
  },
} as const;

export type DictKey = keyof (typeof DICT)["zh-TW"];

interface UiState {
  lang: Lang;
  setLang: (lang: Lang) => void;
}

export const useUiStore = create<UiState>((set) => ({
  lang: (localStorage.getItem("ui_lang") as Lang) || "zh-TW",
  setLang: (lang) => {
    localStorage.setItem("ui_lang", lang);
    document.documentElement.lang = lang;
    set({ lang });
  },
}));

/** 元件內使用：const t = useT(); t("upload") — lang 變更時自動重渲染 */
export function useT() {
  const lang = useUiStore((s) => s.lang);
  return DICT[lang];
}
