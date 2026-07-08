import styles from "../SettingsModal.module.css";
import { type ToolInfo } from "../../../api/client";
import { useT } from "../../../i18n";

export function ToolsSection({ tools }: { tools: ToolInfo[] }) {
  const t = useT();
  return (
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
  );
}
