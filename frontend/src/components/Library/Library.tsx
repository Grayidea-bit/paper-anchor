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
  renameProject,
  uploadDocument,
  type Doc,
  type Project,
  type Usage,
} from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";
import { useT } from "../../i18n";

const PROCESSING = new Set(["uploaded", "parsing", "embedding", "digesting"]);

export function Library() {
  const t = useT();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
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

  const groups: { project: Project | null; docs: Doc[] }[] = [
    ...projects.map((p) => ({
      project: p as Project | null,
      docs: docs.filter((d) => d.project_id === p.id),
    })),
    { project: null, docs: docs.filter((d) => d.project_id == null) },
  ];

  return (
    <div className={styles.library}>
      <div className={styles.uploadBox}>
        <button
          className={styles.uploadBtn}
          disabled={uploading}
          onClick={() => fileInput.current?.click()}
        >
          {uploading ? t.uploading : t.upload}
        </button>
        <button className={styles.scopeChatBtn} onClick={openLibraryChat}>
          {t.libraryChat}
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
        {error && <p className={styles.error}>{error}</p>}
      </div>

      {groups.map(({ project, docs: groupDocs }) => (
        <section key={project?.id ?? "unassigned"} className={styles.group}>
          <div className={styles.groupHeader}>
            <h2 className={styles.groupTitle}>
              {project ? project.name : t.unassigned}
              <span className={styles.groupCount}>{groupDocs.length}</span>
            </h2>
            {project && (
              <div className={styles.groupActions}>
                <button
                  className={styles.groupChatBtn}
                  onClick={() => openProjectChat(project.id, project.name)}
                >
                  {t.projectChat}
                </button>
                <button
                  className={styles.groupIconBtn}
                  title={t.renameProject}
                  onClick={() => {
                    const name = prompt(t.projectNamePlaceholder, project.name);
                    if (name?.trim()) act(() => renameProject(project.id, name.trim()));
                  }}
                >
                  ✎
                </button>
                <button
                  className={styles.groupIconBtn}
                  title={t.deleteProjectTitle}
                  onClick={() => act(() => deleteProject(project.id))}
                >
                  ✕
                </button>
              </div>
            )}
          </div>
          <ul className={styles.list}>
            {groupDocs.map((d) => (
              <DocRow
                key={d.id}
                doc={d}
                projects={projects}
                onOpen={() => openDocument(d.id)}
                onAssign={(pid) => act(() => assignProject(d.id, pid))}
                onDelete={() => act(() => deleteDocument(d.id))}
              />
            ))}
            {groupDocs.length === 0 && (
              <p className={styles.empty}>
                {project ? t.noProjectDocs : t.emptyLibrary}
              </p>
            )}
          </ul>
        </section>
      ))}

      {creatingProject ? (
        <form
          className={styles.newProjectForm}
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

      {usage && (usage.prompt_tokens > 0 || usage.completion_tokens > 0) && (
        <p className={styles.usage}>
          {t.totalUsage}：in {usage.prompt_tokens.toLocaleString()} / out{" "}
          {usage.completion_tokens.toLocaleString()}
        </p>
      )}
    </div>
  );
}

function DocRow({
  doc,
  projects,
  onOpen,
  onAssign,
  onDelete,
}: {
  doc: Doc;
  projects: Project[];
  onOpen: () => void;
  onAssign: (projectId: number | null) => void;
  onDelete: () => void;
}) {
  const t = useT();
  const statusLabel = t[`status_${doc.status}` as const];
  return (
    <li className={styles.item}>
      <button className={styles.docBtn} disabled={doc.status !== "ready"} onClick={onOpen}>
        <span className={styles.docTitle}>{doc.title || doc.filename}</span>
        <span className={styles.docMeta}>
          {doc.page_count > 0 && (
            <span className={styles.metaPages}>
              {doc.page_count} {t.pages}
            </span>
          )}
          <span className={styles.badge} data-status={doc.status}>
            {statusLabel}
          </span>
          {doc.status === "failed" && doc.error_msg && (
            <span className={styles.errNote}>{doc.error_msg}</span>
          )}
        </span>
      </button>
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
