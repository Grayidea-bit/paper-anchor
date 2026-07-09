import { useState, type Dispatch, type SetStateAction } from "react";
import styles from "../SettingsModal.module.css";
import {
  CHAT_BACKEND_OPTIONS,
  EMBED_SOURCE_OPTIONS,
  type ChatBackend,
  type EmbedSource,
  type SettingsPatch,
  type SettingsView,
} from "../../../api/client";
import { useT } from "../../../i18n";
import { useBackupStore } from "../../../stores/backupStore";

interface LlmSectionProps {
  view: SettingsView | null;
  patch: SettingsPatch;
  setPatch: Dispatch<SetStateAction<SettingsPatch>>;
  onClearClaudeToken: () => void | Promise<void>;
}

/** embed_source 選項 → i18n 標籤鍵（同 CHAT_BACKEND_OPTIONS 顯示先例） */
const EMBED_SOURCE_LABEL_KEY: Record<
  EmbedSource,
  "settingsEmbedAuto" | "settingsEmbedNim" | "settingsEmbedLocal"
> = {
  auto: "settingsEmbedAuto",
  nim: "settingsEmbedNim",
  local: "settingsEmbedLocal",
};

export function LlmSection({ view, patch, setPatch, onClearClaudeToken }: LlmSectionProps) {
  const t = useT();
  const [showReembedConfirm, setShowReembedConfirm] = useState(false);

  // reembed 進度沿用 backup 服務層鎖（三方互斥，見 D12）：讀同一個 backupStore status，
  // BackupSection 的進度條元件不可共用（分頁不同），故這裡自帶一條輕量進度顯示
  const backupStatus = useBackupStore((s) => s.status);
  const backupError = useBackupStore((s) => s.error);
  const runReembedNow = useBackupStore((s) => s.runReembed);

  const field = (key: "llm_base_url" | "llm_chat_model") =>
    patch[key] !== undefined ? patch[key] : (view?.[key] ?? "");

  const backend: ChatBackend = patch.chat_backend ?? view?.chat_backend ?? "openai";

  const modelsListText =
    patch.llm_chat_models !== undefined
      ? patch.llm_chat_models.join("\n")
      : (view?.llm_chat_models ?? []).join("\n");

  const savedEmbedSource: EmbedSource = view?.embed_source ?? "auto";
  const embedSource: EmbedSource = patch.embed_source ?? savedEmbedSource;
  const embedSourceDirty = embedSource !== savedEmbedSource;

  // 三方共用鎖（backup/restore/reembed，見 D12）：任一進行中即不可觸發重建
  const reembedRunning = backupStatus?.running === true && backupStatus.operation === "reembed";
  const canReembed = backupStatus?.running !== true;

  return (
    <>
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{t.settingsLlm}</h3>
      <div className={styles.segmented}>
        {CHAT_BACKEND_OPTIONS.map((o) => (
          <button
            key={o.value}
            className={backend === o.value ? styles.segActive : styles.segBtn}
            onClick={() => setPatch({ ...patch, chat_backend: o.value })}
          >
            {o.value === "openai" ? t.settingsBackendOpenai : t.settingsBackendClaudeSdk}
          </button>
        ))}
      </div>

      {backend === "openai" && (
        <>
          <label className={styles.label}>{t.settingsBaseUrl}</label>
          <input
            className={styles.input}
            autoComplete="off"
            value={field("llm_base_url")}
            placeholder={view?.defaults.llm_base_url ?? ""}
            onChange={(e) => setPatch({ ...patch, llm_base_url: e.target.value })}
          />
          <label className={styles.label}>{t.settingsModelsList}</label>
          <textarea
            className={styles.textarea}
            rows={3}
            autoComplete="off"
            name="llm-models-list"
            value={modelsListText}
            placeholder={(view?.defaults.llm_chat_models ?? []).join("\n")}
            onChange={(e) =>
              setPatch({
                ...patch,
                llm_chat_models: e.target.value
                  .split("\n")
                  .map((line) => line.trim())
                  .filter((line) => line.length > 0),
              })
            }
          />
          <p className={styles.hint}>{t.settingsModelsListHint}</p>
          <label className={styles.label}>{t.settingsApiKey}</label>
          <div className={styles.keyRow}>
            <input
              className={styles.input}
              type="password"
              autoComplete="new-password"
              name="llm-api-key"
              value={patch.llm_api_key ?? ""}
              placeholder={view?.llm_api_key_set ? t.settingsApiKeySet : t.settingsApiKeyUnset}
              onChange={(e) => setPatch({ ...patch, llm_api_key: e.target.value })}
            />
            {view?.llm_api_key_set && (
              <button
                className={styles.miniBtn}
                onClick={() => setPatch({ ...patch, llm_api_key: "" })}
              >
                {t.settingsClearKey}
              </button>
            )}
          </div>
        </>
      )}

      {backend === "claude-sdk" && (
        <>
          <p className={styles.hint}>{t.settingsClaudeSetupHint}</p>
          <label className={styles.label}>{t.settingsClaudeToken}</label>
          <div className={styles.keyRow}>
            <input
              className={styles.input}
              type="password"
              autoComplete="new-password"
              name="claude-oauth-token"
              value={patch.claude_oauth_token ?? ""}
              placeholder={
                view?.claude_oauth_token_set
                  ? t.settingsClaudeTokenSet
                  : t.settingsClaudeTokenPlaceholder
              }
              onChange={(e) => setPatch({ ...patch, claude_oauth_token: e.target.value })}
            />
            {view?.claude_oauth_token_set && (
              <button className={styles.miniBtn} onClick={() => void onClearClaudeToken()}>
                {t.settingsClaudeLogout}
              </button>
            )}
          </div>
          <p className={styles.hint}>{t.settingsClaudeModelInChat}</p>
        </>
      )}
    </section>

    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{t.settingsEmbedTitle}</h3>
      <div className={styles.segmented}>
        {EMBED_SOURCE_OPTIONS.map((o) => (
          <button
            key={o.value}
            className={embedSource === o.value ? styles.segActive : styles.segBtn}
            onClick={() => setPatch({ ...patch, embed_source: o.value })}
          >
            {t[EMBED_SOURCE_LABEL_KEY[o.value]]}
          </button>
        ))}
      </div>
      <p className={styles.hint}>{t.settingsEmbedNote}</p>

      {embedSourceDirty && <p className={styles.restoreWarning}>{t.settingsEmbedSwitchWarning}</p>}

      <button
        className={styles.miniBtn}
        style={{ marginTop: 12 }}
        disabled={!canReembed}
        onClick={() => setShowReembedConfirm(true)}
      >
        {t.settingsEmbedReembedBtn}
      </button>

      {reembedRunning && backupStatus?.progress && (
        <div className={styles.progressWrap}>
          <div className={styles.progressTrack}>
            {backupStatus.progress.total > 0 ? (
              <div
                className={styles.progressFill}
                style={{
                  width: `${Math.min(100, (backupStatus.progress.current / backupStatus.progress.total) * 100)}%`,
                }}
              />
            ) : (
              <div className={styles.progressIndeterminate} />
            )}
          </div>
          <p className={styles.hint}>
            {t.settingsOperationReembed} · {backupStatus.progress.current}/{backupStatus.progress.total}
          </p>
        </div>
      )}
      {backupError && <p className={styles.error}>{backupError}</p>}
    </section>

    {showReembedConfirm && (
      <div
        className={styles.overlay}
        onMouseDown={(e) => e.target === e.currentTarget && setShowReembedConfirm(false)}
      >
        <div
          className={styles.confirmDialog}
          role="dialog"
          aria-label={t.settingsEmbedReembedConfirmTitle}
        >
          <p className={styles.confirmTitle}>{t.settingsEmbedReembedConfirmTitle}</p>
          <ul className={styles.confirmList}>
            <li>{t.settingsEmbedReembedConfirmPoint1}</li>
            <li>{t.settingsEmbedReembedConfirmPoint2}</li>
            <li>{t.settingsEmbedReembedConfirmPoint3}</li>
          </ul>
          <div className={styles.confirmActions}>
            <button className={styles.miniBtn} onClick={() => setShowReembedConfirm(false)}>
              {t.cancel}
            </button>
            <button
              className={styles.saveBtn}
              onClick={() => {
                setShowReembedConfirm(false);
                void runReembedNow();
              }}
            >
              {t.settingsEmbedReembedConfirmOk}
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}
