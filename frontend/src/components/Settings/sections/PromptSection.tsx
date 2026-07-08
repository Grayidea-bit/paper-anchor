import type { Dispatch, SetStateAction } from "react";
import styles from "../SettingsModal.module.css";
import { type SettingsPatch, type SettingsView } from "../../../api/client";
import { useT } from "../../../i18n";

interface PromptSectionProps {
  view: SettingsView | null;
  patch: SettingsPatch;
  setPatch: Dispatch<SetStateAction<SettingsPatch>>;
}

export function PromptSection({ view, patch, setPatch }: PromptSectionProps) {
  const t = useT();
  const value = patch.system_prompt_extra !== undefined
    ? patch.system_prompt_extra
    : (view?.system_prompt_extra ?? "");

  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{t.settingsSystemPrompt}</h3>
      <textarea
        className={styles.textarea}
        rows={4}
        maxLength={4000}
        value={value}
        onChange={(e) => setPatch({ ...patch, system_prompt_extra: e.target.value })}
      />
      <p className={styles.hint}>{t.settingsSystemPromptHint}</p>
    </section>
  );
}
