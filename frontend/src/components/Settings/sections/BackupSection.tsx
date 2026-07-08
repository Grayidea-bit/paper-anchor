import { useState, type Dispatch, type SetStateAction } from "react";
import styles from "../SettingsModal.module.css";
import {
  updateSettings,
  type SettingsPatch,
  type SettingsView,
} from "../../../api/client";
import { useT } from "../../../i18n";
import { useBackupStore } from "../../../stores/backupStore";

const BACKUP_INTERVAL_PRESETS = [0, 24, 168];

interface BackupSectionProps {
  view: SettingsView | null;
  patch: SettingsPatch;
  setPatch: Dispatch<SetStateAction<SettingsPatch>>;
  setView: Dispatch<SetStateAction<SettingsView | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
}

/** 階段字串對映 i18n；operation 缺席（後端未部署 M13 擴充）時預設走 backup 分支 */
function phaseLabel(
  t: ReturnType<typeof useT>,
  operation: "backup" | "restore" | null | undefined,
  phase: string,
): string {
  if (operation === "restore") {
    if (phase === "download") return t.settingsRestorePhaseDownload;
    if (phase === "merge") return t.settingsRestorePhaseMerge;
    if (phase === "ingest") return t.settingsRestorePhaseIngest;
  } else {
    if (phase === "pdfs") return t.settingsBackupPhasePdfs;
    if (phase === "db") return t.settingsBackupPhaseDb;
    if (phase === "manifest") return t.settingsBackupPhaseManifest;
  }
  return phase;
}

export function BackupSection({ view, patch, setPatch, setView, setError }: BackupSectionProps) {
  const t = useT();
  const [autoSaving, setAutoSaving] = useState(false);

  const backupStatus = useBackupStore((s) => s.status);
  const backupLoading = useBackupStore((s) => s.loading);
  const backupError = useBackupStore((s) => s.error);
  const runBackupNow = useBackupStore((s) => s.runBackup);
  const connectBackup = useBackupStore((s) => s.connect);
  const disconnectBackup = useBackupStore((s) => s.disconnect);

  const clientIdValue =
    patch.gdrive_client_id !== undefined ? patch.gdrive_client_id : (view?.gdrive_client_id ?? "");

  // 連接依賴已存的 client_id；未存過但輸入框已填也視為可連接（連接時會自動先存）
  const savedClientId = view?.gdrive_client_id ?? "";
  const effectiveClientId = patch.gdrive_client_id !== undefined ? patch.gdrive_client_id : savedClientId;
  const backupConnected = backupStatus?.connected ?? false;
  let connectDisabledReason: string | null = null;
  if (!backupConnected && effectiveClientId.length === 0) {
    connectDisabledReason = t.settingsBackupNeedClientId;
  }
  const canConnect = !backupConnected && !connectDisabledReason && !backupLoading && !autoSaving;
  const canRunBackup = backupConnected && !backupStatus?.running;
  const intervalHours = patch.backup_interval_hours ?? view?.backup_interval_hours ?? 0;

  // 連接時若憑證兩鍵有未存變更 → 先單獨存這兩鍵，成功後從全域 patch 剔除、同步 view，
  // 再走既有 connect（取 auth_url 開新分頁）流程；避免使用者卡在「要先按儲存」的困惑
  const handleConnect = async () => {
    const credPatch: SettingsPatch = {};
    if (patch.gdrive_client_id !== undefined) credPatch.gdrive_client_id = patch.gdrive_client_id;
    if (patch.gdrive_client_secret !== undefined)
      credPatch.gdrive_client_secret = patch.gdrive_client_secret;

    if (Object.keys(credPatch).length > 0) {
      setAutoSaving(true);
      setError(null);
      try {
        const next = await updateSettings(credPatch);
        setView(next);
        setPatch((p) => {
          const { gdrive_client_id: _id, gdrive_client_secret: _secret, ...rest } = p;
          return rest;
        });
      } catch (e) {
        setError((e as Error).message);
        setAutoSaving(false);
        return;
      }
      setAutoSaving(false);
    }
    void connectBackup();
  };

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{t.settingsBackup}</h3>

      <label className={styles.label}>{t.settingsBackupClientId}</label>
      <input
        className={styles.input}
        autoComplete="off"
        value={clientIdValue}
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
            view?.gdrive_client_secret_set ? t.settingsApiKeySet : t.settingsBackupSecretUnset
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
          <button className={styles.miniBtn} disabled={!canConnect} onClick={() => void handleConnect()}>
            {t.settingsBackupConnect}
          </button>
        )}
      </div>
      {!backupConnected && (
        <p className={styles.hint}>{connectDisabledReason ?? t.settingsBackupConnectAutoSave}</p>
      )}

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
            {backupStatus.operation === "restore" ? t.settingsOperationRestore : t.settingsOperationBackup}
            {" · "}
            {phaseLabel(t, backupStatus.operation, backupStatus.progress.phase)}{" "}
            {backupStatus.progress.current}/{backupStatus.progress.total}
          </p>
        </div>
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
  );
}
