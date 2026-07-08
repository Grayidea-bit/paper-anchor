import { useEffect, useState } from "react";
import styles from "./SettingsModal.module.css";
import {
  getSettings,
  getTools,
  getUsage,
  updateSettings,
  type SettingsPatch,
  type SettingsView,
  type ToolInfo,
  type Usage,
} from "../../api/client";
import { useT } from "../../i18n";
import { useBackupStore } from "../../stores/backupStore";
import { UsageSection } from "./sections/UsageSection";
import { LlmSection } from "./sections/LlmSection";
import { PromptSection } from "./sections/PromptSection";
import { BackupSection } from "./sections/BackupSection";
import { AppearanceSection } from "./sections/AppearanceSection";
import { ToolsSection } from "./sections/ToolsSection";

type NavKey = "usage" | "llm" | "prompt" | "backup" | "appearance" | "tools";
type NavLabelKey =
  | "settingsNavUsage"
  | "settingsNavLlm"
  | "settingsNavPrompt"
  | "settingsNavBackup"
  | "settingsNavAppearance"
  | "settingsNavTools";

const NAV_ITEMS: { key: NavKey; labelKey: NavLabelKey }[] = [
  { key: "usage", labelKey: "settingsNavUsage" },
  { key: "llm", labelKey: "settingsNavLlm" },
  { key: "prompt", labelKey: "settingsNavPrompt" },
  { key: "backup", labelKey: "settingsNavBackup" },
  { key: "appearance", labelKey: "settingsNavAppearance" },
  { key: "tools", labelKey: "settingsNavTools" },
];

// 分類旁的未儲存圓點：patch 中的鍵屬於哪個分類（見 nav dirty-dot 規格）
const SECTION_DIRTY_KEYS: Record<NavKey, (keyof SettingsPatch)[]> = {
  usage: [],
  llm: [
    "llm_base_url",
    "llm_api_key",
    "llm_chat_model",
    "llm_chat_models",
    "chat_backend",
    "claude_oauth_token",
  ],
  prompt: ["system_prompt_extra"],
  backup: ["gdrive_client_id", "gdrive_client_secret", "backup_interval_hours"],
  appearance: ["translation_target_lang"],
  tools: [],
};

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const t = useT();

  const [activeNav, setActiveNav] = useState<NavKey>("usage");
  const [view, setView] = useState<SettingsView | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  // dirty-tracking：只送使用者改過的欄位（undefined=未動）
  const [patch, setPatch] = useState<SettingsPatch>({});
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchBackupStatus = useBackupStore((s) => s.fetchStatus);
  const stopBackupPolling = useBackupStore((s) => s.stopPolling);

  useEffect(() => {
    getSettings().then(setView).catch((e: Error) => setError(e.message));
    getTools().then(setTools).catch(() => undefined);
  }, []);

  // 備份狀態：開啟時抓一次；元件卸載（modal 關閉）時停止任何輪詢，避免洩漏 interval
  // 放在殼層而非 BackupSection：切換左 nav 分類不應中斷輪詢
  useEffect(() => {
    void fetchBackupStatus();
    return () => stopBackupPolling();
  }, [fetchBackupStatus, stopBackupPolling]);

  // 用量：開啟期間每 5 秒輪詢（同樣放殼層，與目前顯示的分類無關）
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

  const isSectionDirty = (key: NavKey) =>
    SECTION_DIRTY_KEYS[key].some((k) => Object.prototype.hasOwnProperty.call(patch, k));

  return (
    <div className={styles.overlay} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal} role="dialog" aria-label={t.settings}>
        <div className={styles.header}>
          <span className={styles.title}>{t.settings}</span>
          <button className={styles.close} onClick={onClose} title={t.close}>
            ✕
          </button>
        </div>
        <div className={styles.layout}>
          <nav className={styles.nav}>
            {NAV_ITEMS.map((item) => (
              <button
                key={item.key}
                className={activeNav === item.key ? styles.navItemActive : styles.navItem}
                onClick={() => setActiveNav(item.key)}
              >
                <span>{t[item.labelKey]}</span>
                {isSectionDirty(item.key) && <span className={styles.dirtyDot} />}
              </button>
            ))}
          </nav>
          <div className={styles.panel}>
            {activeNav === "usage" && <UsageSection usage={usage} />}
            {activeNav === "llm" && (
              <LlmSection
                view={view}
                patch={patch}
                setPatch={setPatch}
                onClearClaudeToken={clearClaudeToken}
              />
            )}
            {activeNav === "prompt" && <PromptSection view={view} patch={patch} setPatch={setPatch} />}
            {activeNav === "backup" && (
              <BackupSection
                view={view}
                patch={patch}
                setPatch={setPatch}
                setView={setView}
                setError={setError}
              />
            )}
            {activeNav === "appearance" && (
              <AppearanceSection view={view} patch={patch} setPatch={setPatch} />
            )}
            {activeNav === "tools" && <ToolsSection tools={tools} />}

            {error && <p className={styles.error}>{error}</p>}
          </div>
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
