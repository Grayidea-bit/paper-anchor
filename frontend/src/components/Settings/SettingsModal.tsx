import { useEffect, useState } from "react";
import styles from "./SettingsModal.module.css";
import {
  CHAT_BACKEND_OPTIONS,
  getSettings,
  getTools,
  getUsage,
  updateSettings,
  type ChatBackend,
  type SettingsPatch,
  type SettingsView,
  type ToolInfo,
  type Usage,
} from "../../api/client";
import {
  LANG_OPTIONS,
  THEME_OPTIONS,
  TRANSLATION_TARGET_LANG_OPTIONS,
  useT,
  useUiStore,
} from "../../i18n";

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const t = useT();
  const lang = useUiStore((s) => s.lang);
  const setLang = useUiStore((s) => s.setLang);
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);

  const [view, setView] = useState<SettingsView | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  // dirty-tracking：只送使用者改過的欄位（undefined=未動）
  const [patch, setPatch] = useState<SettingsPatch>({});
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSettings().then(setView).catch((e: Error) => setError(e.message));
    getTools().then(setTools).catch(() => undefined);
  }, []);

  // 用量：開啟期間每 5 秒輪詢
  useEffect(() => {
    const load = () => getUsage().then(setUsage).catch(() => undefined);
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const dirty = Object.keys(patch).length > 0;

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setError(null);
    try {
      const next = await updateSettings(patch);
      setView(next);
      setPatch({});
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const field = (key: "llm_base_url" | "llm_chat_model" | "system_prompt_extra") =>
    (patch[key] !== undefined ? patch[key] : view?.[key] ?? "");

  const backend: ChatBackend = patch.chat_backend ?? view?.chat_backend ?? "openai";

  const modelsListText =
    patch.llm_chat_models !== undefined
      ? patch.llm_chat_models.join("\n")
      : (view?.llm_chat_models ?? []).join("\n");

  const clearClaudeToken = async () => {
    setSaving(true);
    setError(null);
    try {
      const next = await updateSettings({ claude_oauth_token: "" });
      setView(next);
      setPatch((p) => {
        const { claude_oauth_token: _drop, ...rest } = p;
        return rest;
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.overlay} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal} role="dialog" aria-label={t.settings}>
        <div className={styles.header}>
          <span className={styles.title}>{t.settings}</span>
          <button className={styles.close} onClick={onClose} title={t.close}>
            ✕
          </button>
        </div>
        <div className={styles.body}>
          {/* 用量 */}
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{t.settingsUsage}</h3>
            <div className={styles.usageRow}>
              <div className={styles.usageCell}>
                <span className={styles.usageValue}>{usage?.rpm ?? "–"}</span>
                <span className={styles.usageLabel}>{t.settingsRpm}</span>
              </div>
              <div className={styles.usageCell}>
                <span className={styles.usageValue}>
                  {usage ? `${(usage.prompt_tokens / 1000).toFixed(0)}K / ${(usage.completion_tokens / 1000).toFixed(0)}K` : "–"}
                </span>
                <span className={styles.usageLabel}>{t.settingsTokens}（in / out）</span>
              </div>
            </div>
          </section>

          {/* Chat LLM */}
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
                    placeholder={
                      view?.llm_api_key_set ? t.settingsApiKeySet : t.settingsApiKeyUnset
                    }
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
                    <button className={styles.miniBtn} onClick={() => void clearClaudeToken()}>
                      {t.settingsClaudeLogout}
                    </button>
                  )}
                </div>
                <p className={styles.hint}>{t.settingsClaudeModelInChat}</p>
              </>
            )}
          </section>

          {/* 附加 system prompt */}
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{t.settingsSystemPrompt}</h3>
            <textarea
              className={styles.textarea}
              rows={4}
              maxLength={4000}
              value={field("system_prompt_extra")}
              onChange={(e) => setPatch({ ...patch, system_prompt_extra: e.target.value })}
            />
            <p className={styles.hint}>{t.settingsSystemPromptHint}</p>
          </section>

          {/* 語言 / 主題（選項陣列驅動） */}
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{t.settingsLanguage}</h3>
            <div className={styles.segmented}>
              {LANG_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  className={lang === o.value ? styles.segActive : styles.segBtn}
                  onClick={() => setLang(o.value)}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <h3 className={styles.sectionTitle} style={{ marginTop: 14 }}>
              {t.settingsTheme}
            </h3>
            <div className={styles.segmented}>
              {THEME_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  className={theme === o.value ? styles.segActive : styles.segBtn}
                  onClick={() => setTheme(o.value)}
                >
                  {t[o.labelKey]}
                </button>
              ))}
            </div>
            <h3 className={styles.sectionTitle} style={{ marginTop: 14 }}>
              {t.translationTargetLang}
            </h3>
            <div className={styles.segmented}>
              {TRANSLATION_TARGET_LANG_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  className={
                    (patch.translation_target_lang ??
                      view?.translation_target_lang ??
                      "繁體中文") === o.value
                      ? styles.segActive
                      : styles.segBtn
                  }
                  onClick={() => setPatch({ ...patch, translation_target_lang: o.value })}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </section>

          {/* 工具 */}
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{t.settingsTools}</h3>
            {tools.map((tool) => (
              <div key={tool.name} className={styles.toolRow}>
                <span className={styles.toolName}>{tool.name}</span>
                <span className={styles.toolDesc}>{tool.description}</span>
              </div>
            ))}
            <p className={styles.hint}>{t.settingsToolsHint}</p>
          </section>

          {error && <p className={styles.error}>{error}</p>}
        </div>
        <div className={styles.footer}>
          <button className={styles.saveBtn} disabled={!dirty || saving} onClick={() => void save()}>
            {savedFlash ? t.saved : t.save}
          </button>
        </div>
      </div>
    </div>
  );
}
