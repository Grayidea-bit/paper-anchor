import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
import { useT, useUiStore } from "../../i18n";
import { projectColor } from "../Library/Library";

type LocalMessage = Omit<Message, "id" | "created_at"> & {
  pending?: boolean;
  /** 思考摘要（僅本次串流，未持久化） */
  reasoning?: string;
  startedAt?: number;
  thoughtSeconds?: number;
  /** 工具活動（僅本次串流）：如 "keyword_search:done" */
  toolEvents?: string[];
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
  const [attached, setAttached] = useState<Attached | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chatBackend, setChatBackend] = useState<ChatBackend>("openai");
  const [llmChatModels, setLlmChatModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastFailedRef = useRef<{ question: string; selection: Attached | null } | null>(null);

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

  const sendQuestion = useCallback(
    async (question: string, selection: Attached | null) => {
      if (!question || convId === null || streaming) return;
      setError(null);
      setStreaming(true);
      const sel = selection
        ? { text: selection.text, chunk_id: selection.chunkId }
        : undefined;
      const startedAt = Date.now();
      setMessages((prev) => [
        ...prev,
        { role: "user", content: question, citations: [], selection: sel },
        { role: "assistant", content: "", citations: [], pending: true, startedAt },
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
          { language: lang, selection: sel },
        );
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setStreaming(false);
      }
    },
    [convId, streaming, lang],
  );

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
      inputRef.current?.focus();
      return;
    }
    const question = {
      explain: t.presetExplain,
      translate: t.presetTranslate,
      critique: t.presetCritique,
    }[req.preset];
    void sendQuestion(question, sel);
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
      if (c.document_id != null && c.document_id !== documentId) {
        jumpToDocument(c.document_id, { page: c.page, bboxList: c.bbox_list });
      } else {
        jumpTo({ page: c.page, bboxList: c.bbox_list });
      }
    },
    [jumpTo, jumpToDocument, documentId],
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
              {m.pending && m.content === "" && (
                <ThinkingCard startedAt={m.startedAt ?? Date.now()} reasoning={m.reasoning} />
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
      <div className={styles.inputRow}>
        <button
          className={styles.newConvBtn}
          title={t.newConversation}
          disabled={streaming}
          onClick={() => void newConversation()}
        >
          ＋
        </button>
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
        <textarea
          ref={inputRef}
          className={styles.input}
          placeholder={t.inputPlaceholder}
          rows={2}
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
        <button
          className={styles.send}
          title={t.send}
          disabled={streaming || !input.trim() || convId === null}
          onClick={() => void send()}
        >
          {streaming ? "…" : "↑"}
        </button>
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
  const crossDoc = c?.document_id != null && c.document_id !== documentId;
  const title = c
    ? crossDoc && c.document_title
      ? `${c.document_title} · p.${c.page}`
      : t.jumpToPage(c.page)
    : t.citationPending;
  // 同文獻＝上標式 p.N；跨文獻＝填色「標題 · p.N」；解析中＝原始標籤淡色
  const text = c
    ? crossDoc
      ? `${c.document_title ? shortTitle(c.document_title) : "?"} · p.${c.page}`
      : display ?? `p.${c.page}`
    : display ?? String(label);
  return (
    <button
      className={c ? (crossDoc ? styles.citeChipCross : styles.citeChip) : styles.citeChipInactive}
      disabled={!c}
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
