# T-EM-00 — 本地 Embedding 煙霧測試決策記錄 / Local Embedding Smoke-Test Record

> 執行者 Opus。全部數字為 **docker compose `api` 容器（python:3.11-slim、24 vCPU、16 GB RAM）內實測**，非引用網路資料。
> All numbers are **measured inside the `api` container** (python:3.11-slim, 24 vCPU, 16 GB RAM), not quoted from articles.
> 資料集 Corpus：DB 內真實 114 chunks / 3 篇 arXiv 論文（doc3 EdgeZSAD、doc4 PaSTe、doc5 SubspaceAD），平均 ~1.3k 字元/chunk。
> 只讀不寫：本測試**未 UPDATE chunks 表**，本地向量僅存在記憶體。No table was mutated; local vectors lived in RAM only.

---

## 拍板 / Decision

**採用 `BAAI/bge-m3`（fastembed `add_custom_model` 註冊，CLS pooling + normalize，dim=1024）。**
**Adopt `BAAI/bge-m3`** via fastembed custom-model registration (CLS pooling + normalization, 1024-dim).

| 項目 | 決定 |
|---|---|
| 模型 `LOCAL_EMBED_MODEL` | `BAAI/bge-m3` |
| fastembed 版本 pin | `fastembed==0.8.0`（requirements）|
| 註冊方式 | **非原生**——須 `TextEmbedding.add_custom_model(...)`（見下方 §6 程式碼）|
| 維度 | 1024（`VECTOR(1024)` 零 migration，回傳前 assert==1024）|
| query/passage 前綴 | **兩者皆免前綴**（對稱模型；D5 陷阱不適用 BGE-M3）|
| 推論設定（省 RAM）| `threads=8, batch_size=4`（見 §4）|
| 建議最低記憶體 | 容器 **≥ 4 GB**（峰值 ~2.5 GB；README 標註 + compose `mem_limit` 選配）|

**一句話理由**：BGE-M3 是本計畫原定首選、MIT 授權、原生多語（中文 query→英文論文 cross-lingual 是本產品剛需）、**免前綴**（比 NIM/e5 少一個「用錯不報錯只默默劣化」的陷阱）、相似度分佈健康（0.50–0.71，鑑別度高），且對 5 個中文 query 的 top-1 命中在 Q2/Q3/Q4 明顯優於 NIM 基準。唯一代價是它不在 fastembed 原生清單，需 ~10 行 `add_custom_model` 樣板。

---

## 1. fastembed 支援清單核實 / Support-list verification

`fastembed==0.8.0`，`TextEmbedding.list_supported_models()` 共 **30 個 dense 模型**。

- **關鍵發現**：計畫原本設想的兩個候選 **都不在原生清單**：
  - `BAAI/bge-m3` → **ABSENT**（dense/sparse/late-interaction 三個 registry 全查無）
  - `snowflake/snowflake-arctic-embed-l-v2.0`（arctic v2）→ **ABSENT**（只有 v1 的 `snowflake-arctic-embed-l`，dim 1024，**英文專用**）
- `TextEmbedding.add_custom_model(...)` **存在**，可用官方 ONNX 掛載 BGE-M3（見 §6）。BAAI/bge-m3 HF repo 有 `onnx/model.onnx` + `onnx/model.onnx_data`，容器內可達。

**原生 1024 維候選**（僅這些維度零 migration）：

| 模型 | 多語 | 授權 | 前綴需求 | size(GB) | 備註 |
|---|---|---|---|---|---|
| intfloat/multilingual-e5-large | ✅ ~100 lang | MIT | **必要** query:/passage: | 2.24 | fastembed 0.8 改成 mean pooling（發警告，與原版 CLS 行為不同）|
| jinaai/jina-embeddings-v3 | ✅ ~100 lang | **CC-BY-NC-4.0（非商用）** | 免（task-based）| 2.29 | 授權對未來分享有風險，淘汰 |
| BAAI/bge-large-en-v1.5 | ❌ 英文 | MIT | 必要 | 1.2 | cross-lingual 不適用 |
| snowflake-arctic-embed-l | ❌ 英文 | Apache | 必要 | 1.02 | 同上 |
| mixedbread-ai/mxbai-embed-large-v1 | ❌ 英文 | Apache | — | 0.64 | 同上 |
| thenlper/gte-large | ❌ 英文 | MIT | — | 1.2 | 同上 |

> 中文 query→英文論文是剛需 → 英文專用模型全部出局。原生多語 1024 維只剩 e5-large（MIT）與 jina-v3（非商用）。BGE-M3 需 custom-model。

**int8/量化變體**：fastembed 對 bge-m3 / e5-large **無原生量化變體**（只有 `nomic-embed-text-v1.5-Q` 這種與本案無關的）。BGE-M3 官方 ONNX 為 fp32。社群 int8 bge-m3 存在但增風險，v1 先用 fp32，量化列為日後優化。

---

## 2. wheel 體積 / Image increment

`pip install fastembed` 對 site-packages：**601 MB → 676 MB（+75 MB）**。
主要來自 onnxruntime；其餘 fastembed / tokenizers / huggingface_hub / onnx / flatbuffers / coloredlogs / mmh3 / py-rust-stemmers。
→ **image 增量 ~75 MB**（模型權重 **不進 image**，走 `models:` volume）。

---

## 3. 各候選實測（threads=4, batch_size=8，除非另註）/ Per-model measurements

| 指標 | **BGE-M3**（custom） | multilingual-e5-large（原生） |
|---|---|---|
| dim==1024 | ✅ | ✅ |
| 授權 | MIT | MIT |
| 模型下載大小（cache du）| ~2.18 GB（onnx+onnx_data） | ~2.15 GB |
| load+warmup 時間 | 38.7 s | 39.5 s |
| 載入後 RSS | 1.59 GB | 1.58 GB |
| 全 114 passage 嵌入 | 107.8 s（1.1/s）| 54.5 s（2.1/s，此列為 threads=24）|
| 32 段批次 | 22.3 s | 14.6 s（threads=24）|
| 單句 query 延遲（中位）| 32 ms | 34 ms |
| 相似度分佈（top-5 區間）| **0.50–0.71（鑑別度佳）** | 0.80–0.85（壓縮、鑑別度差）|
| pooling | CLS（如設計）| mean（fastembed 0.8 竄改，發警告）|
| 前綴 | 免 | 必要 |

> e5-large 的 threads=24 較快（2.1/s）但 RAM 爆（見 §4）；同 threads 下兩者速度相近。

---

## 4. RAM 峰值結論（關鍵）/ RAM peak (critical)

**onnxruntime 預設吃滿全部 24 vCPU，arena 記憶體暴衝**。實測 e5-large：

| threads | batch | 峰值 RSS | 吞吐 |
|---|---|---|---|
| 24（預設）| 8 | **6.61 GB** ⚠️ | 2.1/s |
| 4 | 8 | 2.12 GB | 1.3/s |
| 1 | 8 | 2.11 GB | 0.4/s |

BGE-M3：

| threads | batch | 峰值 RSS | 吞吐 |
|---|---|---|---|
| 4 | 8 | 3.38 GB | 1.1/s |
| **8** | **4** | **2.53 GB** | **1.7/s**（建議點）|

**結論**：
1. **務必顯式設定 `threads`**（別用預設），否則單機記憶體可能瞬間 +6 GB。建議 `threads=8`。
2. `batch_size=4` 比 8 明顯降峰值（BGE-M3 3.38 GB → 2.53 GB）且因並行度高反而更快。→ **T-EM-01 本地路徑用 `threads=8, batch_size=4`**。
3. 建議 compose 給 api 容器 `mem_limit` 或至少 README 標「本地 embedding 模式最低 4 GB RAM」。峰值 ~2.5 GB + 常駐 app ~0.3 GB + 緩衝。

---

## 5. 檢索品質 spot check（5 中文 query × 114 真 chunks）/ Retrieval spot check

方法：本地模型記憶體內重嵌全 114 chunks（passage）+ 中文 query 各算 cosine top-5；NIM 基準用 `llm.embed_query`（NIM `nv-embedqa-e5-v5`）算 query 向量後對 DB 現有向量 top-5，比 overlap。**NIM 非 ground truth，僅參照**。

| Query | BGE-M3 top-1（判讀）| overlap vs NIM |
|---|---|---|
| 這篇論文的主要貢獻 | Acknowledgements/References（**弱**，泛問題撈到致謝）| 0/5 |
| edge 上 zero-shot 異常偵測 | **EdgeZSAD 標題頁（精準）** | 3/5 |
| few-shot 不訓練如何運作 | **"Few-shot… Training-free vision-based"（精準）** | 2/5 |
| 提升推論效率降低延遲 | **"edge architectures… inference reductions"（精準）** | 0/5 |
| 使用哪些資料集與評估指標 | "AD methods PatchCore… evaluation parameters"（相關）| 0/5 |
| **平均 overlap** | | **BGE-M3 1.0/5、e5-large 1.2/5** |

**判讀**（overlap 低具誤導性，須看實際內容）：
- overlap 低是因為 **NIM 基準本身在 Q4/Q5 表現差**——它對「效率/資料集」問題回傳原始數字表格（"Candle 94.4 ± 0.8…"），語意檢索失焦；BGE-M3 反而撈到方法/結論段，**明顯更合理**。
- BGE-M3 在 Q2/Q3/Q4 的 top-1 精準命中；只有 Q1（極泛問題「主要貢獻」）撈到致謝段偏弱——這是泛查詢的共通難點，NIM 在 Q1 也只是撈到另一篇的貢獻句、非跨篇最佳。
- BGE-M3 相似度分佈 0.50–0.71（有鑑別度）> e5-large 的 0.80–0.85（幾乎所有段落都 0.8x，難分高下）。

→ **BGE-M3 檢索品質可用且穩健，數處優於 NIM 基準**。中文 query 對英文 chunk 的 cross-lingual 命中良好，驗證多語能力足夠上線。

---

## 6. 給 T-EM-01 的交接 / Handoff to T-EM-01

**BGE-M3 非 fastembed 原生**，`local_embed.py` 懶載入時**必須先註冊**再實例化：

```python
from fastembed import TextEmbedding
from fastembed.common.model_description import PoolingType, ModelSource

TextEmbedding.add_custom_model(
    model="BAAI/bge-m3",
    pooling=PoolingType.CLS,          # BGE-M3 用 CLS，非 mean
    normalization=True,               # 回傳已正規化，cosine 即點積
    sources=ModelSource(hf="BAAI/bge-m3"),
    dim=1024,
    model_file="onnx/model.onnx",
    additional_files=["onnx/model.onnx_data"],  # 外部權重，缺了會炸
    size_in_gb=2.2,
)
model = TextEmbedding(
    model_name="BAAI/bge-m3",
    cache_dir=settings.embed_cache_dir,   # /data/models（volume）
    threads=8,                            # 別用預設！預設吃 24 核 → RAM +6GB
)
```

- 嵌入呼叫用 `batch_size=4`：`model.embed(texts, batch_size=4)`。
- **前綴**：BGE-M3 對稱、**query/passage 皆不加前綴**。`embed_passages`/`embed_query` 仍維持兩個獨立入口（計畫要求、未來換模型的保險），但本地路徑兩者都直接 encode 原文，不套 D5 那種 `"query: "` 前綴。回傳前 `assert dim==1024`。
- **懶載入 + `asyncio.to_thread`**：模型 init（load+warmup）~39 s、常駐 RSS ~1.6 GB，務必懶載入（首次用到才載）且推論走 `to_thread`，別阻塞事件迴圈、別拖慢 lifespan 啟動。
- **首次下載**：~2.2 GB 從 HuggingFace（`onnx/model.onnx` + `onnx/model.onnx_data`）；需網路；下載失敗應拋 `LLMError` 走既有 failed 狀態機。volume 快取後重建容器免重下。
- requirements 加 `fastembed==0.8.0`（連帶 onnxruntime 等 ~75 MB image 增量）。
- config 加 `embed_cache_dir="/data/models"` + compose 新 `models:` volume（計畫 §A 已定）。

**清理狀態**：實驗用的臨時 `pip install fastembed`、模型 cache（`/tmp/fe_cache`，4.3 GB）、暫存腳本**已全數從容器移除**，容器回到乾淨狀態（`import fastembed` 已 ModuleNotFoundError）。正式依賴由 T-EM-01 寫入 requirements + volume，不需保留本次暫存。

---

## 7. 風險 / Risks

| 風險 | 緩解 |
|---|---|
| BGE-M3 需 custom-model 註冊（非一行常數）| §6 樣板 ~10 行；pin `fastembed==0.8.0` 防 API 漂移；T-EM-01 單元測試斷言 dim==1024 + 註冊成功 |
| onnxruntime 預設多執行緒 RAM 爆（+6 GB）| **顯式 `threads=8` + `batch_size=4`**；文件標最低 4 GB；compose `mem_limit` 選配 |
| 首次下載 ~2.2 GB / 需網路 | volume 快取一次性；失敗走 failed 可重試；README 明示 |
| fastembed 未來版本移除 add_custom_model API | 版本 pin；若升級須回歸本測試 |
| 混模型向量污染（NIM↔BGE-M3 維度同為 1024，直接混用會壞檢索）| 計畫既有的切換警告 + reembed 重建 + backup/restore/reembed 同鎖（M14 A/C 已涵蓋）|
| int8 量化未採用（fp32 較大較慢）| v1 用 fp32 求穩；量化列日後優化，非上線阻塞 |
