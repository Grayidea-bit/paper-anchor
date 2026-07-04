# 驗收測試集（fixtures）

引用命中率測試與各里程碑驗收使用的真實論文（來源：arXiv）。

**PDF 不入版控**（arXiv 論文的散布授權屬於各論文作者，不隨本 repo 重新散布）。
取得測試集：

```bash
python docs/fixtures/download.py
```

| 檔案 | arXiv ID | 標題 | 備註 |
|---|---|---|---|
| 2410.11591v1.pdf | 2410.11591 | PASTE: Improving the Efficiency of…（雙行標題，僅取首行） | 13 頁 |
| 2602.23013v3.pdf | 2602.23013 | SubspaceAD: Training-Free Few-Shot Anomaly Detection | 8.5MB / 14 頁，圖多，測解析效能（實測 7s ready） |
| 2606.16119v1.pdf | 2606.16119 | EdgeZSAD: Practical Zero-Shot Anomaly Detection on Edge Devices | 9 頁（實測 4s ready、29 chunks） |

## 用途

- **M1 DoD**：三篇皆能成功解析（chunk 含 page/bbox），上傳→ready ≤ 30s。
- **M3 引用命中測試**：每篇各準備 5 個問題，驗證回答引用點擊後跳轉位置正確。
- 版面多樣性不足時（三篇皆 arXiv 模板），再補充單欄期刊版面與含大量公式的論文。

## 規則

- 此目錄的 PDF 為驗收基準，**不可刪除或替換**；新增可以。
- M1 完成後，由 Haiku 執行解析並回填上表標題。
