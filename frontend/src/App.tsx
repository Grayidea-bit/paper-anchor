import { useEffect, useState } from "react";
import styles from "./App.module.css";
import { ChatPane } from "./components/ChatPane/ChatPane";
import { PDFPane } from "./components/PDFPane/PDFPane";
import { getHealth, type Health } from "./api/client";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <h1 className={styles.title}>AI 文獻導讀</h1>
        <span className={styles.status}>
          {health === null ? "API 未連線" : `API ✓ / DB ${health.db ? "✓" : "✗"}`}
        </span>
      </header>
      <main className={styles.panes}>
        <PDFPane />
        <ChatPane />
      </main>
    </div>
  );
}
