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
import { useReaderStore } from "../../stores/readerStore";

type LocalMessage = Omit<Message, "id" | "created_at"> & { pending?: boolean };

const CITATION_SPLIT = /(\[C\d+\])/g;

export function ChatPane() {
  const documentId = useReaderStore((s) => s.documentId);

  if (documentId === null) {
    return (
      <section className={styles.pane} aria-label="對話面板">
        <div className={styles.emptyWrap}>
          <p className={styles.hint}>上傳文獻後，可在此與 LLM 討論內容</p>
        </div>
      </section>
    );
  }
  return <Chat key={documentId} documentId={documentId} />;
}

function Chat({ documentId }: { documentId: number }) {
  const jumpTo = useReaderStore((s) => s.jumpTo);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [convId, setConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [language, setLanguage] = useState(
    () => localStorage.getItem("answer_lang") ?? "zh-TW",
  );
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 初始化：導讀 + 最近對話與歷史（驗收指標 4：重開瀏覽器可續聊）
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

  // 導讀還沒好 → 輪詢
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

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question || convId === null || streaming) return;
    setInput("");
    setError(null);
    setStreaming(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question, citations: [] },
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
            // 沒有任何內容的 assistant 泡泡直接移除
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              return last?.role === "assistant" && !last.content
                ? prev.slice(0, -1)
                : [...prev.slice(0, -1), { ...last, pending: false }];
            });
          },
        },
        { language },
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStreaming(false);
    }
  }, [convId, input, streaming, language]);

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
          <div
            key={i}
            className={m.role === "user" ? styles.userMsg : styles.assistantMsg}
          >
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
            {m.pending && m.content === "" && <span className={styles.cursor}>▍</span>}
          </div>
        ))}
        {error && <p className={styles.error}>{error}</p>}
        <div ref={bottomRef} />
      </div>
      <div className={styles.inputRow}>
        <button
          className={styles.newConvBtn}
          title="開新對話"
          disabled={streaming}
          onClick={() => void newConversation()}
        >
          ＋
        </button>
        <select
          className={styles.langSelect}
          title="回答語言"
          value={language}
          onChange={(e) => {
            setLanguage(e.target.value);
            localStorage.setItem("answer_lang", e.target.value);
          }}
        >
          <option value="zh-TW">中</option>
          <option value="en">EN</option>
        </select>
        <textarea
          className={styles.input}
          placeholder="就這篇文獻提問…（Enter 送出，Shift+Enter 換行）"
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
          {streaming ? "…" : "送出"}
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
  citations,
  onCite,
}: {
  index: number;
  citations: Citation[];
  onCite: ContentProps["onCite"];
}) {
  const active = citations.some((c) => c.chunk_index === index);
  return (
    <button
      className={active ? styles.citeChip : styles.citeChipInactive}
      disabled={!active}
      title={active ? "跳到原文" : "引用解析中…"}
      onClick={() => onCite(index, citations)}
    >
      {index}
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
    <p className={styles.msgText}>
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
  const [collapsed, setCollapsed] = useState(false);
  const [requested, setRequested] = useState(false);

  if (!digest) {
    return (
      <div className={styles.digestCard}>
        <p className={styles.digestPending}>
          📖 導讀產生中…
          {!requested && (
            <button
              className={styles.retryBtn}
              onClick={() => {
                setRequested(true);
                void regenerateDigest(documentId).catch(() => setRequested(false));
              }}
            >
              重新產生
            </button>
          )}
        </p>
      </div>
    );
  }
  return (
    <div className={styles.digestCard}>
      <button className={styles.digestHeader} onClick={() => setCollapsed(!collapsed)}>
        📖 導讀 {collapsed ? "▸" : "▾"}
      </button>
      {!collapsed && (
        <>
          <p className={styles.digestTldr}>{digest.tldr}</p>
          {digest.sections.map((s) => (
            <div key={s.key} className={styles.digestSection}>
              <p className={styles.digestTitle}>{s.title}</p>
              <p className={styles.digestText}>
                {s.text}
                {s.citations.map((c) => (
                  <button
                    key={c.chunk_index}
                    className={styles.citeChip}
                    title={`跳到第 ${c.page} 頁`}
                    onClick={() => onCite(c.chunk_index, s.citations)}
                  >
                    p.{c.page}
                  </button>
                ))}
              </p>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
