import styles from "../SettingsModal.module.css";
import { type Usage } from "../../../api/client";
import { useT } from "../../../i18n";

export function UsageSection({ usage }: { usage: Usage | null }) {
  const t = useT();
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{t.settingsUsage}</h3>
      <div className={styles.usageRow}>
        <div className={styles.usageCell}>
          <span className={styles.usageValue}>{usage?.rpm ?? "–"}</span>
          <span className={styles.usageLabel}>{t.settingsRpm}</span>
        </div>
        <div className={styles.usageCell}>
          <span className={styles.usageValue}>
            {usage
              ? `${(usage.prompt_tokens / 1000).toFixed(0)}K / ${(usage.completion_tokens / 1000).toFixed(0)}K`
              : "–"}
          </span>
          <span className={styles.usageLabel}>{t.settingsTokens}（in / out）</span>
        </div>
      </div>
    </section>
  );
}
