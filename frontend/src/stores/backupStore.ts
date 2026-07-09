import { create } from "zustand";
import * as api from "../api/client";

const POLL_MS = 2000;
const CONNECT_TIMEOUT_MS = 120_000;

export interface BackupState {
  status: api.BackupStatus | null;
  loading: boolean;
  error: string | null;
  fetchStatus: () => Promise<void>;
  runBackup: () => Promise<void>;
  runRestore: () => Promise<void>;
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

// interval id 存在 module scope（非 store state）：zustand state 只放可序列化資料，
// timer 控制屬於副作用，跨 create() 呼叫維持單例即可
let statusPollId: ReturnType<typeof setInterval> | null = null;
let connectPollId: ReturnType<typeof setInterval> | null = null;

function clearStatusPoll() {
  if (statusPollId !== null) {
    clearInterval(statusPollId);
    statusPollId = null;
  }
}

function clearConnectPoll() {
  if (connectPollId !== null) {
    clearInterval(connectPollId);
    connectPollId = null;
  }
}

export const useBackupStore = create<BackupState>((set, get) => ({
  status: null,
  loading: false,
  error: null,

  fetchStatus: async () => {
    // 與其他入口（runBackup/runRestore/connect/disconnect）一致：一律在動作開頭
    // 清掉舊 error，不要只綁在「成功才清」——否則上一次動作留下的 error 會在本次
    // fetchStatus 失敗（例如網路瞬斷）時繼續殘留，混進下一個動作的畫面。
    set({ error: null });
    try {
      const status = await api.getBackupStatus();
      set({ status });
      // 偵測到（本機或其他來源觸發的）備份仍在跑，接手輪詢直到完成
      if (status.running) get().startPolling();
    } catch (err) {
      console.error("Failed to fetch backup status:", err);
    }
  },

  runBackup: async () => {
    set({ error: null });
    try {
      await api.runBackup();
      await get().fetchStatus();
      // 202 早於背景任務取鎖，fetchStatus 可能還看不到 running；
      // 輪詢在 !running 時會自清，無條件啟動以消除競態
      get().startPolling();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  runRestore: async () => {
    set({ error: null });
    try {
      await api.restoreBackup();
      await get().fetchStatus();
      get().startPolling();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  connect: async () => {
    set({ error: null, loading: true });
    let authUrl: string;
    try {
      const res = await api.getBackupAuthUrl();
      authUrl = res.auth_url;
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
      return;
    }
    window.open(authUrl, "_blank", "noopener,noreferrer");

    clearConnectPoll();
    const deadline = Date.now() + CONNECT_TIMEOUT_MS;
    connectPollId = setInterval(() => {
      void (async () => {
        try {
          const status = await api.getBackupStatus();
          set({ status });
          if (status.connected) {
            clearConnectPoll();
            set({ loading: false });
            return;
          }
        } catch (err) {
          console.error("Failed to poll backup status during connect:", err);
        }
        if (Date.now() >= deadline) {
          clearConnectPoll();
          set({ loading: false, error: "connect_timeout" });
        }
      })();
    }, POLL_MS);
  },

  disconnect: async () => {
    set({ error: null });
    try {
      await api.disconnectBackup();
      await get().fetchStatus();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  startPolling: () => {
    if (statusPollId !== null) return;
    statusPollId = setInterval(() => {
      void (async () => {
        try {
          const status = await api.getBackupStatus();
          set({ status });
          if (!status.running) clearStatusPoll();
        } catch (err) {
          console.error("Failed to poll backup status:", err);
        }
      })();
    }, POLL_MS);
  },

  stopPolling: () => {
    clearStatusPoll();
    // 只有「connect 輪詢真的被中止」才重置 loading：
    // 一般 status 輪詢（backup/restore 進度）與 connect 無關，不該誤碰 loading。
    // 不這樣做的話，connect() 設 loading:true 後若 SettingsModal 在輪到 connected
    // 前卸載（呼叫本函式清掉 connectPollId），loading 會永遠卡 true——
    // module 級單例跨開關存活，導致重開設定頁「連接」鈕永久 disabled。
    const hadConnectPoll = connectPollId !== null;
    clearConnectPoll();
    if (hadConnectPoll) set({ loading: false });
  },
}));
