import type { Dispatch, SetStateAction } from "react";
import styles from "../SettingsModal.module.css";
import {
  CHAT_BACKEND_OPTIONS,
  type ChatBackend,
  type SettingsPatch,
  type SettingsView,
} from "../../../api/client";
import { useT } from "../../../i18n";

interface LlmSectionProps {
  view: SettingsView | null;
  patch: SettingsPatch;
  setPatch: Dispatch<SetStateAction<SettingsPatch>>;
  onClearClaudeToken: () => void | Promise<void>;
}

export function LlmSection({ view, patch, setPatch, onClearClaudeToken }: LlmSectionProps) {
  const t = useT();

  const field = (key: "llm_base_url" | "llm_chat_model") =>
    patch[key] !== undefined ? patch[key] : (view?.[key] ?? "");

  const backend: ChatBackend = patch.chat_backend ?? view?.chat_backend ?? "openai";

  const modelsListText =
    patch.llm_chat_models !== undefined
      ? patch.llm_chat_models.join("\n")
      : (view?.llm_chat_models ?? []).join("\n");

  return (
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
          <p className={styles.hint}>{t.settingsEmbedNote}</p>
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
  );
}
