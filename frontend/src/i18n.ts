import { create } from "zustand";

export type Lang = "zh-TW" | "en";
export type Theme = "dark" | "light";

/** 選項陣列驅動（新增語言/主題＝在此加一項） */
export const LANG_OPTIONS: { value: Lang; label: string }[] = [
  { value: "zh-TW", label: "中文" },
  { value: "en", label: "English" },
];

export const THEME_OPTIONS: { value: Theme; labelKey: "themeDark" | "themeLight" }[] = [
  { value: "dark", labelKey: "themeDark" },
  { value: "light", labelKey: "themeLight" },
];

const DICT = {
  "zh-TW": {
    appName: "Paper Anchor · 文獻導讀",
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
    retry: "重試",
    totalUsage: "累計 token",
    libraryChat: "全庫問答",
    projectChat: "專案問答",
    unassigned: "未分類",
    newProject: "＋新專案",
    projectNamePlaceholder: "專案名稱…",
    renameProject: "改名",
    deleteProjectTitle: "刪除專案（文獻會回到未分類）",
    scopeLibrary: "範圍：全部文獻",
    scopeProject: (name: string) => `專案：${name}`,
    noProjectDocs: "此專案尚無文獻",
    allDocuments: "全部文獻",
    projectsLabel: "專案",
    connected: "connected",
    offline: "offline",
    papers: (n: number) => `${n} 篇`,
    thinking: (s: number) => `思考中 · ${s}s`,
    thoughtFor: (s: number) => `已思考 ${s}s`,
    anchorTag: "錨點",
    themeToggle: "深/淺色",
    settings: "設定",
    settingsUsage: "用量",
    settingsRpm: "每分鐘請求",
    settingsTokens: "累計 token",
    settingsLlm: "Chat LLM 來源",
    settingsBaseUrl: "Base URL",
    settingsApiKey: "API Key",
    settingsApiKeySet: "已設定（留空維持不變）",
    settingsApiKeyUnset: "未設定（使用 .env）",
    settingsClearKey: "清除",
    settingsModel: "模型",
    settingsSystemPrompt: "附加系統提示詞",
    settingsSystemPromptHint: "附加在內建守則之後，對所有對話生效",
    settingsLanguage: "語言（介面與回答）",
    settingsTheme: "主題",
    themeDark: "深色",
    themeLight: "淺色",
    settingsTools: "LLM 工具",
    settingsToolsHint: "新增工具：複製 backend/app/tools/template_tool.py",
    settingsEmbedNote: "Embedding 來源固定於 .env（更換需重建全部向量）",
    save: "儲存",
    saved: "已儲存",
    close: "關閉",
    toolActivity: (name: string) => `🔧 ${name}`,
  },
  en: {
    appName: "Paper Anchor",
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
    retry: "Retry",
    totalUsage: "Total tokens",
    libraryChat: "Ask all papers",
    projectChat: "Ask project",
    unassigned: "Unfiled",
    newProject: "＋ New project",
    projectNamePlaceholder: "Project name…",
    renameProject: "Rename",
    deleteProjectTitle: "Delete project (papers return to Unfiled)",
    scopeLibrary: "Scope: all papers",
    scopeProject: (name: string) => `Project: ${name}`,
    noProjectDocs: "No papers in this project yet",
    allDocuments: "All papers",
    projectsLabel: "PROJECTS",
    connected: "connected",
    offline: "offline",
    papers: (n: number) => `${n} papers`,
    thinking: (s: number) => `thinking · ${s}s`,
    thoughtFor: (s: number) => `thought for ${s}s`,
    anchorTag: "anchor",
    themeToggle: "Theme",
    settings: "Settings",
    settingsUsage: "Usage",
    settingsRpm: "Requests / min",
    settingsTokens: "Total tokens",
    settingsLlm: "Chat LLM source",
    settingsBaseUrl: "Base URL",
    settingsApiKey: "API Key",
    settingsApiKeySet: "Set (leave blank to keep)",
    settingsApiKeyUnset: "Not set (using .env)",
    settingsClearKey: "Clear",
    settingsModel: "Model",
    settingsSystemPrompt: "Extra system prompt",
    settingsSystemPromptHint: "Appended after built-in rules; applies to all chats",
    settingsLanguage: "Language (UI & answers)",
    settingsTheme: "Theme",
    themeDark: "Dark",
    themeLight: "Light",
    settingsTools: "LLM tools",
    settingsToolsHint: "Add tools: copy backend/app/tools/template_tool.py",
    settingsEmbedNote: "Embedding source is fixed in .env (changing it requires re-indexing)",
    save: "Save",
    saved: "Saved",
    close: "Close",
    toolActivity: (name: string) => `🔧 ${name}`,
  },
} as const;

export type DictKey = keyof (typeof DICT)["zh-TW"];

interface UiState {
  lang: Lang;
  theme: Theme;
  setLang: (lang: Lang) => void;
  setTheme: (theme: Theme) => void;
}

export const useUiStore = create<UiState>((set) => ({
  lang: (localStorage.getItem("ui_lang") as Lang) || "zh-TW",
  theme: (localStorage.getItem("ui_theme") as Theme) || "dark",
  setLang: (lang) => {
    localStorage.setItem("ui_lang", lang);
    document.documentElement.lang = lang;
    set({ lang });
  },
  setTheme: (theme) => {
    localStorage.setItem("ui_theme", theme);
    const apply = () => {
      document.documentElement.dataset.theme = theme;
    };
    // 主題漸變：優先用 View Transitions（整頁交叉淡化，含 canvas）；
    // 不支援時退回全域 CSS transition
    const doc = document as Document & {
      startViewTransition?: (cb: () => void) => unknown;
    };
    if (doc.startViewTransition) {
      doc.startViewTransition(apply);
    } else {
      const root = document.documentElement;
      root.classList.add("theme-transition");
      apply();
      window.setTimeout(() => root.classList.remove("theme-transition"), 450);
    }
    set({ theme });
  },
}));

/** 元件內使用：const t = useT(); t("upload") — lang 變更時自動重渲染 */
export function useT() {
  const lang = useUiStore((s) => s.lang);
  return DICT[lang];
}
