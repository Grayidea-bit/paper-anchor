import { useCallback, useState } from "react";
import type { AnnotationColor } from "../../api/client";
import styles from "./PDFPane.module.css";

const ANNOT_COLORS: AnnotationColor[] = ["amber", "terracotta", "sage", "slate"];

/**
 * 共用選色元件：平常只顯示「當前色」單顆圓點；點擊展開 4 色可選。
 * 選色後收合回單顆，但不觸發外部關閉（呼叫端需自行 stopPropagation 外層選單）。
 * 用於：SelMenu 的「當前色」選色區、annotMenu 的換色區。
 */
export function ColorDots({
  current,
  onChange,
  title,
}: {
  current: AnnotationColor;
  onChange: (color: AnnotationColor) => void;
  title?: string;
}) {
  const [open, setOpen] = useState(false);

  const toggle = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setOpen((v) => !v);
  }, []);

  const pick = useCallback(
    (e: React.MouseEvent, color: AnnotationColor) => {
      e.stopPropagation();
      onChange(color);
      setOpen(false);
    },
    [onChange],
  );

  if (!open) {
    return (
      <button
        type="button"
        className={styles.colorDotToggle}
        onClick={toggle}
        onMouseUp={(e) => e.stopPropagation()}
        title={title}
      >
        <span
          className={styles.colorSwatch}
          data-active
          style={{ background: `var(--annot-${current})` }}
          aria-hidden="true"
        />
      </button>
    );
  }

  return (
    <span className={styles.colorSwatches} onMouseUp={(e) => e.stopPropagation()}>
      {ANNOT_COLORS.map((c) => (
        <button
          key={c}
          type="button"
          className={styles.colorSwatch}
          data-active={c === current || undefined}
          style={{ background: `var(--annot-${c})` }}
          aria-label={c}
          aria-pressed={c === current}
          onClick={(e) => pick(e, c)}
        />
      ))}
    </span>
  );
}
