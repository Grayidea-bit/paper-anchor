import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./Library.module.css";
import {
  assignProject,
  createProject,
  deleteDocument,
  deleteProject,
  getUsage,
  listDocuments,
  listProjects,
  reingestDocument,
  renameProject,
  uploadDocument,
  type Doc,
  type Project,
  type Usage,
} from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";
import { useT } from "../../i18n";

const PROCESSING = new Set(["uploaded", "parsing", "embedding", "digesting"]);

/** 專案色點：以 id 決定，穩定不隨排序變 */
const DOT_COLORS = ["#d97e6b", "#7a8fae", "#6fae7f", "#b98ac9", "#c9a86b", "#8fb3ae"];
export function projectColor(id: number): string {
  return DOT_COLORS[id % DOT_COLORS.length];
}

type Selected = "all" | "unassigned" | number;

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export function Library() {
  const t = useT();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [selected, setSelected] = useState<Selected>("all");
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newName, setNewName] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const openDocument = useReaderStore((s) => s.openDocument);
  const openProjectChat = useReaderStore((s) => s.openProjectChat);
  const openLibraryChat = useReaderStore((s) => s.openLibraryChat);

  const refresh = useCallback(() => {
    listDocuments().then(setDocs).catch((e: Error) => setError(e.message));
    listProjects().then(setProjects).catch(() => undefined);
    getUsage().then(setUsage).catch(() => undefined);
  }, []);

  useEffect(refresh, [refresh]);

  useEffect(() => {
    if (!docs.some((d) => PROCESSING.has(d.status))) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [docs, refresh]);

  const onUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      await uploadDocument(file);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const act = (fn: () => Promise<unknown>) => {
    setError(null);
    fn().then(refresh).catch((e: Error) => setError(e.message));
  };

  const selectedProject =
    typeof selected === "number" ? projects.find((p) => p.id === selected) ?? null : null;
  const shownDocs = docs.filter((d) =>
    selected === "all"
      ? true
      : selected === "unassigned"
        ? d.project_id == null
        : d.project_id === selected,
  );
  const unassignedCount = docs.filter((d) => d.project_id == null).length;
  const mainTitle =
    selected === "all"
      ? t.allDocuments
      : selected === "unassigned"
        ? t.unassigned
        : selectedProject?.name ?? "";

  return (
    <div className={styles.library}>
      {/* ---- 側欄 ---- */}
      <aside className={styles.sidebar}>
        <div className={styles.uploadWrap}>
          <button
            className={styles.uploadBtn}
            disabled={uploading}
            onClick={() => fileInput.current?.click()}
          >
            {uploading ? t.uploading : `↑ ${t.upload}`}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onUpload(f);
            }}
          />
        </div>
        <nav className={styles.nav}>
          <button
            className={selected === "all" ? styles.navItemActive : styles.navItem}
            onClick={() => setSelected("all")}
          >
            {t.allDocuments}
            <span className={styles.navCount}>{docs.length}</span>
          </button>
          <div className={styles.navLabel}>{t.projectsLabel}</div>
          {projects.map((p) => (
            <button
              key={p.id}
              className={selected === p.id ? styles.navItemActive : styles.navItem}
              onClick={() => setSelected(p.id)}
            >
              <span className={styles.dot} style={{ background: projectColor(p.id) }} />
              <span className={styles.navName}>{p.name}</span>
              <span className={styles.navCount}>{p.document_count ?? 0}</span>
            </button>
          ))}
          <button
            className={selected === "unassigned" ? styles.navItemActive : styles.navItem}
            onClick={() => setSelected("unassigned")}
          >
            <span className={styles.dot} style={{ background: "var(--text-faint)" }} />
            <span className={styles.navName}>{t.unassigned}</span>
            <span className={styles.navCount}>{unassignedCount}</span>
          </button>
          {creatingProject ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const name = newName.trim();
                if (name) {
                  act(() => createProject(name));
                  setNewName("");
                  setCreatingProject(false);
                }
              }}
            >
              <input
                autoFocus
                className={styles.newProjectInput}
                placeholder={t.projectNamePlaceholder}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onBlur={() => setCreatingProject(false)}
              />
            </form>
          ) : (
            <button className={styles.newProjectBtn} onClick={() => setCreatingProject(true)}>
              {t.newProject}
            </button>
          )}
        </nav>
        {usage && (usage.prompt_tokens > 0 || usage.completion_tokens > 0) && (
          <div className={styles.usage}>
            tokens in {fmtTokens(usage.prompt_tokens)}
            <br />
            tokens out {fmtTokens(usage.completion_tokens)}
          </div>
        )}
      </aside>

      {/* ---- 主區 ---- */}
      <div className={styles.main}>
        <div className={styles.mainHeader}>
          <h2 className={styles.mainTitle}>{mainTitle}</h2>
          <span className={styles.mainCount}>{t.papers(shownDocs.length)}</span>
          <span className={styles.spacer} />
          {selected === "all" && (
            <button className={styles.qaBtn} onClick={openLibraryChat}>
              {t.libraryChat}
            </button>
          )}
          {selectedProject && (
            <>
              <button
                className={styles.qaBtn}
                onClick={() => openProjectChat(selectedProject.id, selectedProject.name)}
              >
                {t.projectChat}
              </button>
              <button
                className={styles.textBtn}
                onClick={() => {
                  const name = prompt(t.projectNamePlaceholder, selectedProject.name);
                  if (name?.trim()) act(() => renameProject(selectedProject.id, name.trim()));
                }}
              >
                {t.renameProject}
              </button>
              <button
                className={styles.textBtn}
                title={t.deleteProjectTitle}
                onClick={() => {
                  act(() => deleteProject(selectedProject.id));
                  setSelected("all");
                }}
              >
                {t.delete}
              </button>
            </>
          )}
        </div>
        {error && <p className={styles.error}>{error}</p>}
        <ul className={styles.list}>
          {shownDocs.map((d) => (
            <DocRow
              key={d.id}
              doc={d}
              projects={projects}
              onOpen={() => openDocument(d.id)}
              onAssign={(pid) => act(() => assignProject(d.id, pid))}
              onDelete={() => act(() => deleteDocument(d.id))}
              onReingest={() => act(() => reingestDocument(d.id))}
            />
          ))}
          {shownDocs.length === 0 && (
            <p className={styles.empty}>
              {typeof selected === "number" ? t.noProjectDocs : t.emptyLibrary}
            </p>
          )}
        </ul>
      </div>
    </div>
  );
}

function DocRow({
  doc,
  projects,
  onOpen,
  onAssign,
  onDelete,
  onReingest,
}: {
  doc: Doc;
  projects: Project[];
  onOpen: () => void;
  onAssign: (projectId: number | null) => void;
  onDelete: () => void;
  onReingest: () => void;
}) {
  const t = useT();
  const statusLabel = t[`status_${doc.status}` as const];
  const busy = PROCESSING.has(doc.status);
  return (
    <li className={styles.row}>
      <button className={styles.rowMain} disabled={doc.status !== "ready"} onClick={onOpen}>
        <span className={styles.rowTitle}>{doc.title || doc.filename}</span>
        <span className={styles.rowMeta}>
          {doc.page_count > 0 ? `${doc.page_count} ${t.pages}` : doc.filename}
        </span>
      </button>
      <span className={styles.pill} data-status={doc.status}>
        {busy && <span className={styles.pillDot} />}
        {statusLabel}
        {doc.status === "failed" && doc.error_msg ? ` · ${doc.error_msg.slice(0, 24)}` : ""}
      </span>
      {doc.status === "failed" && (
        <button className={styles.textBtn} title={t.reingestTitle} onClick={onReingest}>
          {t.reingest}
        </button>
      )}
      <select
        className={styles.projectSelect}
        value={doc.project_id ?? ""}
        onChange={(e) => onAssign(e.target.value === "" ? null : Number(e.target.value))}
      >
        <option value="">{t.unassigned}</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <button className={styles.deleteBtn} title={t.delete} onClick={onDelete}>
        ✕
      </button>
    </li>
  );
}
