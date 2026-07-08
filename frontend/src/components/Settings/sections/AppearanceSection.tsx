import type { Dispatch, SetStateAction } from "react";
import styles from "../SettingsModal.module.css";
import { type SettingsPatch, type SettingsView } from "../../../api/client";
import {
  LANG_OPTIONS,
  THEME_OPTIONS,
  TRANSLATION_TARGET_LANG_OPTIONS,
  useT,
  useUiStore,
} from "../../../i18n";

interface AppearanceSectionProps {
  view: SettingsView | null;
  patch: SettingsPatch;
  setPatch: Dispatch<SetStateAction<SettingsPatch>>;
}

export function AppearanceSection({ view, patch, setPatch }: AppearanceSectionProps) {
  const t = useT();
  const lang = useUiStore((s) => s.lang);
  const setLang = useUiStore((s) => s.setLang);
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);

  return (
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
        onChange={(e) => setPatch({ ...patch, translation_target_lang: e.target.value })}
      />
      <datalist id="translation-lang-suggestions">
        {TRANSLATION_TARGET_LANG_OPTIONS.map((o) => (
          <option key={o.value} value={o.value} />
        ))}
      </datalist>
    </section>
  );
}
