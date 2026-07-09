import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Square, Languages } from "lucide-react";
import styles from "./ChatPane.module.css";
import {
  CLAUDE_MODELS,
  createConversation,
  createScopedConversation,
  getDocument,
  getSettings,
  listConversations,
  listMessages,
  listScopedConversations,
  regenerateDigest,
  setConversationModel,
  streamMessage,
  type BBox,
  type ChatBackend,
  type Citation,
  type Conversation,
  type Digest,
  type Message,
} from "../../api/client";
import {
  useReaderStore,
  type ChatContext,
  type SelectionAsk,
} from "../../stores/readerStore";
import { useGlossaryStore } from "../../stores/glossaryStore";
import { useT, useUiStore } from "../../i18n";
import { projectColor } from "../Library/Library";

/** 翻譯回答下方「加入翻譯表」候選：選取過長（>200 字）時不產生候選 */
const MAX_GLOSSARY_TERM_CHARS = 200;
const MAX_GLOSSARY_TRANSLATION_CHARS = 500;
const MAX_GLOSSARY_NOTES_CHARS = 12000;

/** 純標題行判定：整行去掉常見 markdown 標記後，等於「翻譯」/"Translation" 這類單詞標題 */
const HEADING_ONLY_RE = /^(翻譯|譯文|translation)$/i;

/** 剝除單行常見 markdown 標記：粗體/斜體、行首井字標題、行首 bullet／blockquote、反引號、包覆的引號 */
function stripMarkdownInline(line: string): string {
  return line
    .replace(/^#{1,6}\s+/, "")
    .replace(/^>+\s?/, "")
    .replace(/^[-*+]\s+/, "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/_(.+?)_/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim()
    .replace(/^["「『]([\s\S]*)["」』]$/, "$1")
    .trim();
}

/** 從翻譯回答全文抽第一行當譯文：跳過空行、純標題行、以及僅覆誦原術語本身的行
 * （例如 LLM 常見的 `> "term"` 覆誦句，剝除引號/markdown 後與 term 相同就跳過），
 * 剝除 markdown 標記後截斷。抽不到（全空）回傳 null，呼叫端 fallback 送 source_text。 */
function extractFirstLineTranslation(content: string, term?: string): string | null {
  const termNormalized = term?.trim().toLowerCase();
  for (const raw of content.split("\n")) {
    const stripped = stripMarkdownInline(raw);
    if (!stripped) continue;
    if (HEADING_ONLY_RE.test(stripped)) continue;
    if (termNormalized && stripped.toLowerCase() === termNormalized) continue;
    return stripped.slice(0, MAX_GLOSSARY_TRANSLATION_CHARS);
  }
  return null;
}

interface GlossaryCandidate {
  term: string;
  page: number;
  bboxList: BBox[];
  chunkId: number | null;
}

type GlossaryStatus = "idle" | "adding" | "added" | "error";

type LocalMessage = Omit<Message, "id" | "created_at"> & {
  pending?: boolean;
  /** 思考摘要（僅本次串流，未持久化） */
  reasoning?: string;
  startedAt?: number;
  thoughtSeconds?: number;
  /** 工具活動（僅本次串流）：如 "keyword_search:done" */
  toolEvents?: string[];
  /** 使用者主動中斷（保留已收到文字，不視為錯誤） */
  stopped?: boolean;
  /** 翻譯 preset 且帶錨點時掛上的「加入翻譯表」候選（本 session 內有效） */
  glossaryCandidate?: GlossaryCandidate;
  glossaryStatus?: GlossaryStatus;
};
type Attached = { text: string; chunkId: number | null };

const CITATION_SPLIT = /(\[C\d+\])/g;

function contextKey(ctx: ChatContext): string {
  if (ctx.kind === "document") return `doc-${ctx.documentId}`;
  if (ctx.kind === "project") return `project-${ctx.projectId}`;
  return "library";
}

export function ChatPane() {
  const t = useT();
  const chatContext = useReaderStore((s) => s.chatContext);

  if (chatContext === null) {
    return (
      <section className={styles.pane} aria-label="對話面板">
        <div className={styles.emptyWrap}>
          <p className={styles.hint}>{t.chatEmptyHint}</p>
        </div>
      </section>
    );
  }
  return <Chat key={contextKey(chatContext)} context={chatContext} />;
}

function Chat({ context }: { context: ChatContext }) {
  const t = useT();
  const lang = useUiStore((s) => s.lang);
  const documentId = useReaderStore((s) => s.documentId);
  const jumpTo = useReaderStore((s) => s.jumpTo);
  const jumpToDocument = useReaderStore((s) => s.jumpToDocument);
  const closeScopedChat = useReaderStore((s) => s.closeScopedChat);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [convId, setConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [multiline, setMultiline] = useState(false);
  const [attached, setAttached] = useState<Attached | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chatBackend, setChatBackend] = useState<ChatBackend>("openai");
  const [llmChatModels, setLlmChatModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastFailedRef = useRef<{ question: string; selection: Attached | null } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const isDocument = context.kind === "document";

  // 掛載時取來源設定（chat_backend、NIM 模型清單）供模型下拉使用
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((view) => {
        if (cancelled) return;
        setChatBackend(view.chat_backend ?? "openai");
        setLlmChatModels(view.llm_chat_models ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // 初始化：對話串（三種 scope）+ 導讀（僅 document）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let convs: Conversation[];
      if (context.kind === "document") {
        const doc = await getDocument(context.documentId);
        if (cancelled) return;
        setDigest(doc.digest ?? null);
        convs = await listConversations(context.documentId);
      } else if (context.kind === "project") {
        convs = await listScopedConversations("project", context.projectId);
      } else {
        convs = await listScopedConversations("library");
      }
      if (cancelled) return;
      const conv =
        convs[0] ??
        (context.kind === "document"
          ? await createConversation(context.documentId)
          : await createScopedConversation(
              context.kind,
              context.kind === "project" ? context.projectId : undefined,
            ));
      if (cancelled) return;
      setConvId(conv.id);
      setSelectedModel(conv.model ?? null);
      const history = await listMessages(conv.id);
      if (!cancelled) setMessages(history);
    })().catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [context]);

  // 導讀還沒好 → 輪詢（僅 document scope）
  useEffect(() => {
    if (!isDocument || digest) return;
    const docId = (context as Extract<ChatContext, { kind: "document" }>).documentId;
    const timer = setInterval(() => {
      getDocument(docId)
        .then((d) => d.digest && setDigest(d.digest))
        .catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [digest, isDocument, context]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  // textarea 自動長高 + ChatGPT 式版型切換：
  // 單行 → 全部同列；換行（含 Shift+Enter 或軟換行）→ textarea 獨佔上列、控制項落下列。
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
    // 單行 scrollHeight ≈ 34（padding16+line18）；> 44 視為多行
    setMultiline(input.includes("\n") || el.scrollHeight > 44);
  }, [input]);

  const sendQuestion = useCallback(
    async (
      question: string,
      selection: Attached | null,
      glossaryCandidate?: GlossaryCandidate,
    ) => {
      if (!question || convId === null || streaming) return;
      setError(null);
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;
      const sel = selection
        ? { text: selection.text, chunk_id: selection.chunkId }
        : undefined;
      const startedAt = Date.now();
      setMessages((prev) => [
        ...prev,
        { role: "user", content: question, citations: [], selection: sel },
        {
          role: "assistant",
          content: "",
          citations: [],
          pending: true,
          startedAt,
          glossaryCandidate,
          glossaryStatus: glossaryCandidate ? "idle" : undefined,
        },
      ]);
      const patchLast = (patch: (m: LocalMessage) => LocalMessage) =>
        setMessages((prev) => [...prev.slice(0, -1), patch(prev[prev.length - 1])]);
      try {
        await streamMessage(
          convId,
          question,
          {
            onToken: (text) =>
              patchLast((m) => ({
                ...m,
                content: m.content + text,
                thoughtSeconds:
                  m.thoughtSeconds ?? Math.round((Date.now() - startedAt) / 1000),
              })),
            onReasoning: (text) =>
              patchLast((m) => ({ ...m, reasoning: (m.reasoning ?? "") + text })),
            onTool: (te) =>
              patchLast((m) => {
                const events = [...(m.toolEvents ?? [])];
                if (te.status === "start") events.push(`${te.name}:start`);
                else {
                  // start → done/error 就地更新
                  const i = events.lastIndexOf(`${te.name}:start`);
                  if (i >= 0) events[i] = `${te.name}:${te.status}`;
                  else events.push(`${te.name}:${te.status}`);
                }
                return { ...m, toolEvents: events };
              }),
            onCitations: (citations) => patchLast((m) => ({ ...m, citations })),
            onDone: () => patchLast((m) => ({ ...m, pending: false })),
            onError: (message) => {
              if (controller.signal.aborted) return;
              setError(message);
              lastFailedRef.current = { question, selection };
              setMessages((prev) => {
                const last = prev[prev.length - 1];
                return last?.role === "assistant" && !last.content
                  ? prev.slice(0, -1)
                  : [...prev.slice(0, -1), { ...last, pending: false }];
              });
            },
          },
          { language: lang, selection: sel, signal: controller.signal },
        );
        if (controller.signal.aborted) {
          // 使用者主動中斷：保留已收到文字，標記已中斷，不視為錯誤
          patchLast((m) => ({ ...m, pending: false, stopped: true }));
        }
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setStreaming(false);
      }
    },
    [convId, streaming, lang],
  );

  const stopGenerating = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question) return;
    setInput("");
    const sel = attached;
    setAttached(null);
    await sendQuestion(question, sel);
  }, [input, attached, sendQuestion]);

  const retry = useCallback(() => {
    const failed = lastFailedRef.current;
    if (!failed || streaming) return;
    lastFailedRef.current = null;
    setError(null);
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      const secondLast = prev[prev.length - 2];
      // 失敗訊息殿後有兩種形狀：純 user（assistant 內容為空，onError 已移除該筆）；
      // 或 user + 半截 assistant（mid-stream 錯誤保留已收到內容，見 onError）。
      // 後者殿後是 assistant，下面單獨判斷 last 是 user 會失效，
      // 需整組（失敗的 user + 其後未完成 assistant）一起剝除再重送，
      // 否則會在陣列留下 [...,user, 半截assistant, user(重複), 新assistant]。
      if (
        last?.role === "assistant" &&
        secondLast?.role === "user" &&
        secondLast.content === failed.question
      ) {
        return prev.slice(0, -2);
      }
      return last?.role === "user" && last.content === failed.question
        ? prev.slice(0, -1)
        : prev;
    });
    void sendQuestion(failed.question, failed.selection);
  }, [streaming, sendQuestion]);

  // PDF 選取提問：僅 document scope 消費
  const selectionAsk = useReaderStore((s) => s.selectionAsk);
  const clearSelectionAsk = useReaderStore((s) => s.clearSelectionAsk);
  useEffect(() => {
    if (!isDocument || !selectionAsk || convId === null || streaming) return;
    const req: SelectionAsk = selectionAsk;
    clearSelectionAsk();
    const sel = { text: req.text, chunkId: req.chunkId };
    if (req.preset === "free") {
      setAttached(sel);
      // 圈選自動附掛：不搶焦點（使用者可能還在 PDF 側繼續圈選/標註）
      if (!req.auto) inputRef.current?.focus();
      return;
    }
    const question = {
      explain: t.presetExplain,
      translate: t.presetTranslate,
      critique: t.presetCritique,
    }[req.preset];
    // 翻譯 preset 且帶錨點：準備「加入翻譯表」候選（術語過長就不產生候選）
    const glossaryCandidate: GlossaryCandidate | undefined =
      req.preset === "translate" && req.anchor && req.text.length <= MAX_GLOSSARY_TERM_CHARS
        ? {
            term: req.text.slice(0, MAX_GLOSSARY_TERM_CHARS),
            page: req.anchor.page,
            bboxList: req.anchor.bboxList,
            chunkId: req.chunkId,
          }
        : undefined;
    void sendQuestion(question, sel, glossaryCandidate);
  }, [selectionAsk, convId, streaming, isDocument, clearSelectionAsk, sendQuestion, t]);

  const newConversation = useCallback(async () => {
    if (streaming) return;
    const conv =
      context.kind === "document"
        ? await createConversation(context.documentId)
        : await createScopedConversation(
            context.kind,
            context.kind === "project" ? context.projectId : undefined,
          );
    setConvId(conv.id);
    setMessages([]);
    setSelectedModel(null);
  }, [context, streaming]);

  const modelOptions =
    chatBackend === "claude-sdk"
      ? CLAUDE_MODELS
      : llmChatModels.map((m) => ({ value: m, label: m }));

  const changeModel = useCallback(
    async (value: string) => {
      if (convId === null) return;
      const model = value || null;
      setSelectedModel(model);
      try {
        await setConversationModel(convId, model);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [convId],
  );

  const clickCitation = useCallback(
    (label: number, citations: Citation[]) => {
      const c = citations.find((x) => (x.label ?? x.chunk_index) === label);
      if (!c) return;
      // document_id 明確為 null＝還原後查無對應文獻（見 D11 remap 規則）：
      // page/bbox_list 屬於已不存在的文獻，跳到目前文獻只會誤跳，故不動作
      if (c.document_id === null) return;
      if (c.document_id != null && c.document_id !== documentId) {
        jumpToDocument(c.document_id, { page: c.page, bboxList: c.bbox_list });
      } else {
        jumpTo({ page: c.page, bboxList: c.bbox_list });
      }
    },
    [jumpTo, jumpToDocument, documentId],
  );

  const glossaryCreate = useGlossaryStore((s) => s.create);

  /** 翻譯回答下方「＋ 加入翻譯表」：前端直接從回答全文抽第一行當譯文，notes 存全文，
   * 帶 translation 給後端即可直存、不打 LLM，瞬間完成。抽不到譯文（全空）才 fallback
   * 送 source_text 讓後端走舊的 LLM 萃取路徑。
   * glossaryStore.create 失敗時只 console.error 並吞掉例外（不 throw），
   * 故用建立前後的 entries 筆數變化判斷是否成功，藉此讓鈕恢復可重試。 */
  const addAnswerToGlossary = useCallback(
    async (index: number) => {
      const target = messages[index];
      const candidate = target?.glossaryCandidate;
      if (!candidate) return;
      setMessages((prev) =>
        prev.map((m, i) => (i === index ? { ...m, glossaryStatus: "adding" } : m)),
      );
      const before = useGlossaryStore.getState().entries.length;
      const translation = extractFirstLineTranslation(target.content, candidate.term);
      await glossaryCreate({
        term: candidate.term,
        page: candidate.page,
        bbox_list: candidate.bboxList,
        chunk_id: candidate.chunkId,
        ...(translation !== null
          ? { translation, notes: target.content.slice(0, MAX_GLOSSARY_NOTES_CHARS) }
          : { source_text: target.content.slice(0, 8000) }),
      });
      const succeeded = useGlossaryStore.getState().entries.length > before;
      setMessages((prev) =>
        prev.map((m, i) =>
          i === index ? { ...m, glossaryStatus: succeeded ? "added" : "error" } : m,
        ),
      );
    },
    [messages, glossaryCreate],
  );

  return (
    <section className={styles.pane} aria-label="對話面板">
      {!isDocument && (
        <div className={styles.scopeBar}>
          {context.kind === "project" && (
            <span
              className={styles.scopeDot}
              style={{ background: projectColor(context.projectId) }}
            />
          )}
          <span className={styles.scopeLabel}>
            {context.kind === "project" ? t.scopeProject(context.name) : t.scopeLibrary}
          </span>
          <button className={styles.scopeClose} onClick={closeScopedChat}>
            ✕
          </button>
        </div>
      )}
      <div className={styles.messages}>
        {isDocument && (
          <DigestCard
            digest={digest}
            documentId={(context as Extract<ChatContext, { kind: "document" }>).documentId}
            onCite={clickCitation}
          />
        )}
        {messages.map((m, i) => (
          <div key={i} className={styles.entry}>
            <span className={m.role === "user" ? styles.markerQ : styles.markerA}>
              {m.role === "user" ? "Q" : "A"}
            </span>
            <div className={styles.entryBody}>
              {m.role === "user" && m.selection?.text && (
                <blockquote className={styles.selQuote}>
                  {m.selection.text.length > 200
                    ? `${m.selection.text.slice(0, 200)}…`
                    : m.selection.text}
                </blockquote>
              )}
              {m.role === "assistant" && m.thoughtSeconds != null && m.reasoning && (
                <ThoughtToggle seconds={m.thoughtSeconds} reasoning={m.reasoning} />
              )}
              {m.role === "assistant" && m.toolEvents && m.toolEvents.length > 0 && (
                <div className={styles.toolActivity}>
                  {m.toolEvents.map((te, j) => {
                    const [name, status] = te.split(":");
                    return (
                      <span key={j} className={styles.toolActivityItem} data-status={status}>
                        {t.toolActivity(name)}
                        {status === "done" ? " ✓" : status === "error" ? " ✗" : "…"}
                      </span>
                    );
                  })}
                </div>
              )}
              {m.role === "assistant" ? (
                <MarkdownWithCitations
                  content={m.content}
                  citations={m.citations}
                  onCite={clickCitation}
                />
              ) : (
                <PlainWithCitations
                  content={m.content}
                  citations={m.citations}
                  onCite={clickCitation}
                />
              )}
              {m.pending && m.content === "" && !m.stopped && (
                <ThinkingCard startedAt={m.startedAt ?? Date.now()} reasoning={m.reasoning} />
              )}
              {m.stopped && <span className={styles.stoppedTag}>{t.generationStopped}</span>}
              {m.role === "assistant" &&
                !m.pending &&
                !m.stopped &&
                m.glossaryCandidate &&
                m.glossaryStatus && (
                  <button
                    type="button"
                    className={styles.addToGlossaryBtn}
                    data-status={m.glossaryStatus}
                    disabled={m.glossaryStatus === "adding" || m.glossaryStatus === "added"}
                    onClick={() => void addAnswerToGlossary(i)}
                  >
                    {m.glossaryStatus === "added" ? (
                      "✓"
                    ) : (
                      <Languages size={12} strokeWidth={2} />
                    )}
                    {m.glossaryStatus === "adding"
                      ? t.translating
                      : m.glossaryStatus === "added"
                        ? t.addedToGlossary
                        : t.addToGlossary}
                  </button>
                )}
            </div>
          </div>
        ))}
        {error && (
          <div className={styles.error}>
            {error}
            {lastFailedRef.current && !streaming && (
              <button className={styles.retryBtn} onClick={retry}>
                {t.retry}
              </button>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      {attached && (
        <div className={styles.attachedBar}>
          <span className={styles.attachedLabel}>{t.selectedPassage}</span>
          <span className={styles.attachedText}>
            {attached.text.length > 120 ? `${attached.text.slice(0, 120)}…` : attached.text}
          </span>
          <button
            className={styles.attachedDismiss}
            title={t.dismiss}
            onClick={() => setAttached(null)}
          >
            ✕
          </button>
        </div>
      )}
      <div className={`${styles.inputRow}${multiline ? ` ${styles.multiline}` : ""}`}>
        <button
          className={styles.newConvBtn}
          title={t.newConversation}
          disabled={streaming}
          onClick={() => void newConversation()}
        >
          ＋
        </button>
        <textarea
          ref={inputRef}
          className={styles.input}
          placeholder={t.inputPlaceholder}
          rows={1}
          value={input}
          disabled={convId === null}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <div className={styles.trailing}>
          {modelOptions.length > 1 && (
            <select
              className={styles.modelSelect}
              title={t.chatModelLabel}
              aria-label={t.chatModelLabel}
              value={selectedModel ?? ""}
              disabled={convId === null}
              onChange={(e) => void changeModel(e.target.value)}
            >
              <option value="">{t.modelDefault}</option>
              {modelOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          )}
          {streaming ? (
            <button
              className={`${styles.send} ${styles.stop}`}
              title={t.stopGenerating}
              onClick={stopGenerating}
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              className={styles.send}
              title={t.send}
              disabled={!input.trim() || convId === null}
              onClick={() => void send()}
            >
              ↑
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

/** 思考中卡片：計時 + 串流推理摘要（固定高度防跳動） */
function ThinkingCard({ startedAt, reasoning }: { startedAt: number; reasoning?: string }) {
  const t = useT();
  const [seconds, setSeconds] = useState(() =>
    Math.max(0, Math.round((Date.now() - startedAt) / 1000)),
  );
  useEffect(() => {
    const timer = setInterval(
      () => setSeconds(Math.max(0, Math.round((Date.now() - startedAt) / 1000))),
      1000,
    );
    return () => clearInterval(timer);
  }, [startedAt]);
  const tail = reasoning ? reasoning.slice(-600) : "";
  return (
    <div className={styles.thinkingCard}>
      <div className={styles.thinkingHead}>
        <span className={styles.thinkingDot} />
        <span className={styles.thinkingLabel}>{t.thinking(seconds)}</span>
      </div>
      {tail && <p className={styles.reasoningText}>{tail}</p>}
    </div>
  );
}

/** 回答完成後的「已思考 Xs ▸」可展開列 */
function ThoughtToggle({ seconds, reasoning }: { seconds: number; reasoning: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.thoughtWrap}>
      <button className={styles.thoughtToggle} onClick={() => setOpen(!open)}>
        {t.thoughtFor(seconds)} {open ? "▾" : "▸"}
      </button>
      {open && <p className={styles.reasoningFull}>{reasoning}</p>}
    </div>
  );
}

interface ContentProps {
  content: string;
  citations: Citation[];
  onCite: (label: number, citations: Citation[]) => void;
}

function shortTitle(title: string): string {
  const cut = title.split(/[:：—–-]/)[0].trim();
  return cut.length > 18 ? `${cut.slice(0, 18)}…` : cut;
}

function CiteChip({
  label,
  display,
  citations,
  onCite,
}: {
  label: number;
  display?: string;
  citations: Citation[];
  onCite: ContentProps["onCite"];
}) {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);
  const c = citations.find((x) => (x.label ?? x.chunk_index) === label);
  // 還原後查無對應文獻（見 D11）：document_id 明確為 null，不可跳轉，視同解析中樣式禁用
  const orphaned = c != null && c.document_id === null;
  const crossDoc = c?.document_id != null && c.document_id !== documentId;
  const title = c
    ? orphaned
      ? t.citationPending
      : crossDoc && c.document_title
        ? `${c.document_title} · p.${c.page}`
        : t.jumpToPage(c.page)
    : t.citationPending;
  // 同文獻＝上標式 p.N；跨文獻＝填色「標題 · p.N」；解析中／已還原無對應文獻＝原始標籤淡色
  const text = c
    ? orphaned
      ? (display ?? `[${label}]`)
      : crossDoc
        ? `${c.document_title ? shortTitle(c.document_title) : "?"} · p.${c.page}`
        : display ?? `p.${c.page}`
    : display ?? String(label);
  return (
    <button
      className={c && !orphaned ? (crossDoc ? styles.citeChipCross : styles.citeChip) : styles.citeChipInactive}
      disabled={!c || orphaned}
      title={title}
      onClick={() => onCite(label, citations)}
    >
      {text}
    </button>
  );
}

/** assistant 訊息：markdown 渲染；[C123] 先轉成 #cite-123 連結再畫成 chip */
function MarkdownWithCitations({ content, citations, onCite }: ContentProps) {
  const prepared = content.replace(/\[[Cc](\d+)\]/g, "[$1](#cite-$1)");
  return (
    <div className={styles.md}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const m = /^#cite-(\d+)$/.exec(href ?? "");
            if (m) {
              return (
                <CiteChip label={Number(m[1])} citations={citations} onCite={onCite} />
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {prepared}
      </ReactMarkdown>
    </div>
  );
}

/** 純 markdown 渲染（無引用 chip）：供翻譯表懸浮視窗顯示 notes 全文用，
 * 沿用 ChatPane 同一套 react-markdown + remark-gfm + .md 樣式，維持視覺一致。 */
export function SimpleMarkdown({ content }: { content: string }) {
  return (
    <div className={styles.md}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

/** user 訊息：純文字（保留換行），引用標記仍可點 */
function PlainWithCitations({ content, citations, onCite }: ContentProps) {
  return (
    <p className={styles.question}>
      {content.split(CITATION_SPLIT).map((part, i) => {
        const m = /^\[C(\d+)\]$/.exec(part);
        if (!m) return <Fragment key={i}>{part}</Fragment>;
        return (
          <CiteChip key={i} label={Number(m[1])} citations={citations} onCite={onCite} />
        );
      })}
    </p>
  );
}

function DigestCard({
  digest,
  documentId,
  onCite,
}: {
  digest: Digest | null;
  documentId: number;
  onCite: (label: number, citations: Citation[]) => void;
}) {
  const t = useT();
  const lang = useUiStore((s) => s.lang);
  const [collapsed, setCollapsed] = useState(false);
  const [requested, setRequested] = useState(false);

  if (!digest) {
    return (
      <div className={styles.digestCard}>
        <p className={styles.digestPending}>
          {t.digestPending}
          {!requested && (
            <button
              className={styles.retryBtn}
              onClick={() => {
                setRequested(true);
                void regenerateDigest(documentId, lang).catch(() => setRequested(false));
              }}
            >
              {t.regenerate}
            </button>
          )}
        </p>
      </div>
    );
  }
  return (
    <div className={styles.digestCard}>
      <button className={styles.digestHeader} onClick={() => setCollapsed(!collapsed)}>
        <span className={styles.digestLabel}>{t.digest}</span>
        <span className={styles.digestToggle}>{collapsed ? "＋" : "－"}</span>
      </button>
      {!collapsed && (
        <>
          <p className={styles.digestTldr}>{digest.tldr}</p>
          {digest.sections.map((s, i) => (
            <div
              key={s.key}
              className={styles.digestSection}
              style={{ animationDelay: `${i * 70}ms` }}
            >
              <p className={styles.digestTitle}>{s.title}</p>
              <p className={styles.digestText}>
                {s.text}
                {s.citations.map((c) => (
                  <CiteChip
                    key={c.chunk_index}
                    label={c.label ?? c.chunk_index}
                    display={`p.${c.page}`}
                    citations={s.citations}
                    onCite={onCite}
                  />
                ))}
              </p>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
