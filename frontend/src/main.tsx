import ReactDOM from "react-dom/client";
import App from "./App";
import { useUiStore } from "./i18n";
import "./index.css";

document.documentElement.lang = useUiStore.getState().lang;
document.documentElement.dataset.theme = useUiStore.getState().theme;

// 不用 StrictMode：其開發模式的 effect 雙跑會與 pdf.js 的 canvas render
// 互相取消（RenderingCancelledException），文字層也會被清空。
ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
