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
import { useBackupStore } from "../../stores/backupStore";

const BACKUP_INTERVAL_PRESETS = [0, 24, 168];

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

  const backupStatus = useBackupStore((s) => s.status);
  const backupLoading = useBackupStore((s) => s.loading);
  const backupError = useBackupStore((s) => s.error);
  const fetchBackupStatus = useBackupStore((s) => s.fetchStatus);
  const runBackupNow = useBackupStore((s) => s.runBackup);
  const connectBackup = useBackupStore((s) => s.connect);
  const disconnectBackup = useBackupStore((s) => s.disconnect);
  const stopBackupPolling = useBackupStore((s) => s.stopPolling);

  useEffect(() => {
    getSettings().then(setView).catch((e: Error) => setError(e.message));
    getTools().then(setTools).catch(() => undefined);
  }, []);

  // 備份狀態：開啟時抓一次；元件卸載（modal 關閉）時停止任何輪詢，避免洩漏 interval
  useEffect(() => {
    void fetchBackupStatus();
    return () => stopBackupPolling();
  }, [fetchBackupStatus, stopBackupPolling]);

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

  const field = (
    key: "llm_base_url" | "llm_chat_model" | "system_prompt_extra" | "gdrive_client_id",
  ) => (patch[key] !== undefined ? patch[key] : view?.[key] ?? "");

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

  // 連接 Google Drive 依賴後端已存好的 client_id（PUT 後才生效），
  // 未存過或有未儲存變更時 disabled 並提示原因
  const savedClientId = view?.gdrive_client_id ?? "";
  const backupConnected = backupStatus?.connected ?? false;
  let connectDisabledReason: string | null = null;
  if (!backupConnected) {
    if (savedClientId.length === 0) connectDisabledReason = t.settingsBackupNeedClientId;
    else if (dirty) connectDisabledReason = t.settingsBackupNeedSave;
  }
  const canConnect = !backupConnected && !connectDisabledReason && !backupLoading;
  const canRunBackup = backupConnected && !backupStatus?.running;
  const intervalHours = patch.backup_interval_hours ?? view?.backup_interval_hours ?? 0;

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

          {/* 備份（M12 / D10） */}
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{t.settingsBackup}</h3>

            <label className={styles.label}>{t.settingsBackupClientId}</label>
            <input
              className={styles.input}
              autoComplete="off"
              value={field("gdrive_client_id")}
              onChange={(e) => setPatch({ ...patch, gdrive_client_id: e.target.value })}
            />

            <label className={styles.label}>{t.settingsBackupClientSecret}</label>
            <div className={styles.keyRow}>
              <input
                className={styles.input}
                type="password"
                autoComplete="new-password"
                name="gdrive-client-secret"
                value={patch.gdrive_client_secret ?? ""}
                placeholder={
                  view?.gdrive_client_secret_set
                    ? t.settingsApiKeySet
                    : t.settingsBackupSecretUnset
                }
                onChange={(e) => setPatch({ ...patch, gdrive_client_secret: e.target.value })}
              />
              {view?.gdrive_client_secret_set && (
                <button
                  className={styles.miniBtn}
                  onClick={() => setPatch({ ...patch, gdrive_client_secret: "" })}
                >
                  {t.settingsClearKey}
                </button>
              )}
            </div>

            <div className={styles.keyRow} style={{ marginTop: 12, alignItems: "center" }}>
              <span className={styles.statusDot} data-ok={backupConnected ? "true" : "false"} />
              <span className={styles.hint} style={{ marginTop: 0 }}>
                {backupConnected ? t.settingsBackupConnected : t.settingsBackupNotConnected}
              </span>
            </div>
            <div className={styles.keyRow} style={{ marginTop: 8 }}>
              {backupConnected ? (
                <button className={styles.miniBtn} onClick={() => void disconnectBackup()}>
                  {t.settingsBackupDisconnect}
                </button>
              ) : (
                <button
                  className={styles.miniBtn}
                  disabled={!canConnect}
                  onClick={() => void connectBackup()}
                >
                  {t.settingsBackupConnect}
                </button>
              )}
            </div>
            {connectDisabledReason && <p className={styles.hint}>{connectDisabledReason}</p>}

            <label className={styles.label} style={{ marginTop: 14 }}>
              {t.settingsBackupInterval}
            </label>
            <div className={styles.segmented}>
              {BACKUP_INTERVAL_PRESETS.map((h) => (
                <button
                  key={h}
                  className={intervalHours === h ? styles.segActive : styles.segBtn}
                  onClick={() => setPatch({ ...patch, backup_interval_hours: h })}
                >
                  {h === 0 ? t.settingsBackupIntervalOff : `${h}h`}
                </button>
              ))}
            </div>
            <div className={styles.keyRow} style={{ marginTop: 8, alignItems: "center" }}>
              <input
                className={styles.input}
                type="number"
                min={0}
                max={8760}
                value={intervalHours}
                onChange={(e) => {
                  const parsed = parseInt(e.target.value, 10);
                  // clamp 0..8760 對齊後端 Pydantic ge=0/le=8760，避免 422
                  setPatch({
                    ...patch,
                    backup_interval_hours: Number.isFinite(parsed)
                      ? Math.min(8760, Math.max(0, parsed))
                      : 0,
                  });
                }}
              />
              <span className={styles.hint} style={{ marginTop: 0 }}>
                {t.settingsBackupIntervalCustomHours}
              </span>
            </div>

            <button
              className={styles.miniBtn}
              style={{ marginTop: 14 }}
              disabled={!canRunBackup}
              onClick={() => void runBackupNow()}
            >
              {t.settingsBackupRunNow}
            </button>
            {backupStatus?.running && backupStatus.progress && (
              <p className={styles.hint}>
                {t.settingsBackupProgress(
                  backupStatus.progress.phase,
                  backupStatus.progress.current,
                  backupStatus.progress.total,
                )}
              </p>
            )}

            {backupStatus?.last_run ? (
              <p className={backupStatus.last_run.ok ? styles.backupOk : styles.error}>
                {t.settingsBackupLastRun}: {new Date(backupStatus.last_run.at).toLocaleString()}
                {backupStatus.last_run.ok ? " ✓" : ` — ${backupStatus.last_run.error ?? ""}`}
              </p>
            ) : (
              <p className={styles.hint}>{t.settingsBackupNeverRun}</p>
            )}
            {backupError && (
              <p className={styles.error}>
                {backupError === "connect_timeout" ? t.settingsBackupConnectTimeout : backupError}
              </p>
            )}
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
            <input
              className={styles.input}
              type="text"
              maxLength={60}
              list="translation-lang-suggestions"
              placeholder="繁體中文"
              value={patch.translation_target_lang ?? view?.translation_target_lang ?? ""}
              onChange={(e) =>
                setPatch({ ...patch, translation_target_lang: e.target.value })
              }
            />
            <datalist id="translation-lang-suggestions">
              {TRANSLATION_TARGET_LANG_OPTIONS.map((o) => (
                <option key={o.value} value={o.value} />
              ))}
            </datalist>
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
