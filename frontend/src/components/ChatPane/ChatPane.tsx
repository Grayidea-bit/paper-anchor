import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./ChatPane.module.css";
import {
  createConversation,
  getDocument,
  listConversations,
  listMessages,
  regenerateDigest,
  streamMessage,
  type Citation,
  type Digest,
  type Message,
} from "../../api/client";
import { useReaderStore, type SelectionAsk } from "../../stores/readerStore";
import { useT, useUiStore } from "../../i18n";

type LocalMessage = Omit<Message, "id" | "created_at"> & { pending?: boolean };
type Attached = { text: string; chunkId: number | null };

const CITATION_SPLIT = /(\[C\d+\])/g;

export function ChatPane() {
  const t = useT();
  const documentId = useReaderStore((s) => s.documentId);

  if (documentId === null) {
    return (
      <section className={styles.pane} aria-label="對話面板">
        <div className={styles.emptyWrap}>
          <p className={styles.hint}>{t.chatEmptyHint}</p>
        </div>
      </section>
    );
  }
  return <Chat key={documentId} documentId={documentId} />;
}

function Chat({ documentId }: { documentId: number }) {
  const t = useT();
  const lang = useUiStore((s) => s.lang);
  const jumpTo = useReaderStore((s) => s.jumpTo);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [convId, setConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [attached, setAttached] = useState<Attached | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastFailedRef = useRef<{ question: string; selection: Attached | null } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const doc = await getDocument(documentId);
      if (cancelled) return;
      setDigest(doc.digest ?? null);
      const convs = await listConversations(documentId);
      if (cancelled) return;
      const conv = convs[0] ?? (await createConversation(documentId));
      if (cancelled) return;
      setConvId(conv.id);
      const history = await listMessages(conv.id);
      if (!cancelled) setMessages(history);
    })().catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    if (digest) return;
    const timer = setInterval(() => {
      getDocument(documentId)
        .then((d) => d.digest && setDigest(d.digest))
        .catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [digest, documentId]);

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
      setMessages((prev) => [
        ...prev,
        { role: "user", content: question, citations: [], selection: sel },
        { role: "assistant", content: "", citations: [], pending: true },
      ]);
      const patchLast = (patch: (m: LocalMessage) => LocalMessage) =>
        setMessages((prev) => [...prev.slice(0, -1), patch(prev[prev.length - 1])]);
      try {
        await streamMessage(
          convId,
          question,
          {
            onToken: (text) => patchLast((m) => ({ ...m, content: m.content + text })),
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

  // PDF 選取提問：預設動作直接送出，自由提問附掛到輸入區
  const selectionAsk = useReaderStore((s) => s.selectionAsk);
  const clearSelectionAsk = useReaderStore((s) => s.clearSelectionAsk);
  useEffect(() => {
    if (!selectionAsk || convId === null || streaming) return;
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
  }, [selectionAsk, convId, streaming, clearSelectionAsk, sendQuestion, t]);

  const retry = useCallback(() => {
    const failed = lastFailedRef.current;
    if (!failed || streaming) return;
    lastFailedRef.current = null;
    setError(null);
    // 移除失敗殘留的 user 訊息，重送時會重新加入
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      return last?.role === "user" && last.content === failed.question
        ? prev.slice(0, -1)
        : prev;
    });
    void sendQuestion(failed.question, failed.selection);
  }, [streaming, sendQuestion]);

  const newConversation = useCallback(async () => {
    if (streaming) return;
    const conv = await createConversation(documentId);
    setConvId(conv.id);
    setMessages([]);
  }, [documentId, streaming]);

  const clickCitation = useCallback(
    (index: number, citations: Citation[]) => {
      const c = citations.find((x) => x.chunk_index === index);
      if (c) jumpTo({ page: c.page, bboxList: c.bbox_list });
    },
    [jumpTo],
  );

  return (
    <section className={styles.pane} aria-label="對話面板">
      <div className={styles.messages}>
        <DigestCard digest={digest} documentId={documentId} onCite={clickCitation} />
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
                <span className={styles.thinking}>
                  <span />
                  <span />
                  <span />
                </span>
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
          disabled={streaming || !input.trim() || convId === null}
          onClick={() => void send()}
        >
          {streaming ? "…" : t.send}
        </button>
      </div>
    </section>
  );
}

interface ContentProps {
  content: string;
  citations: Citation[];
  onCite: (index: number, citations: Citation[]) => void;
}

function CiteChip({
  index,
  label,
  citations,
  onCite,
}: {
  index: number;
  label?: string;
  citations: Citation[];
  onCite: ContentProps["onCite"];
}) {
  const t = useT();
  const active = citations.some((c) => c.chunk_index === index);
  return (
    <button
      className={active ? styles.citeChip : styles.citeChipInactive}
      disabled={!active}
      title={active ? t.jumpToSource : t.citationPending}
      onClick={() => onCite(index, citations)}
    >
      {label ?? index}
    </button>
  );
}

/** assistant 訊息：markdown 渲染；[C12] 先轉成 #cite-12 連結再畫成 chip */
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
                <CiteChip index={Number(m[1])} citations={citations} onCite={onCite} />
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
          <CiteChip key={i} index={Number(m[1])} citations={citations} onCite={onCite} />
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
  onCite: (index: number, citations: Citation[]) => void;
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
                    index={c.chunk_index}
                    label={`p.${c.page}`}
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
