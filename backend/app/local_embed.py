"""本地 embedding 模型（M14，見 docs/02-architecture.md D12）。

僅 `app/llm.py` 得 import 本模組（CLAUDE.md 鐵律 3）：LLM／embedding 供應商存取
收束於 llm.py 唯一入口，本模組只負責「本地推論」這一種來源的實作細節。

BGE-M3 非 fastembed 原生模型，須先 `add_custom_model` 註冊（CLS pooling + normalize，
MIT）再實例化；決策與實測數字見 docs/plans/M14-smoke-test-record.md。務必顯式
`threads=8`（別用預設，否則 onnxruntime 吃滿全核，RAM 瞬間 +6GB）。

fastembed 只在函式內 import（懶載入）：模組頂層不 import，避免測試環境（主機無
fastembed）在 `import app.llm` 時連帶炸掉；也避免 lifespan 啟動時的載入開銷
（load+warmup ~39s、常駐 RSS ~1.6GB）。

`LLMError` 也在函式內 import（而非模組頂層）：`llm.py` 會 import 本模組，若本模組
在頂層回頭 import `app.llm` 會構成循環 import；函式內延遲 import 在呼叫當下
`app.llm` 早已載入完畢，安全且零副作用。
"""

import asyncio
import threading

from app.config import get_settings

LOCAL_EMBED_MODEL = "BAAI/bge-m3"
LOCAL_EMBED_DIM = 1024

_BATCH_SIZE = 4

_model = None  # 模組級單例（懶載入）
# 首次載入互斥：_get_model 跑在 to_thread 的 worker thread（見 embed_local——載入含
# 2.2GB 下載與 ~39s warmup，放事件迴圈執行緒會卡死整個 API，M14 審查 M1）；併發 ingest
# 首呼會有多條 thread 同時見 _model is None，不加鎖會重複下載/載入（審查 L3）。
_load_lock = threading.Lock()


def _get_model():
    """懶載入單例：首次呼叫才註冊 + 載入模型（含下載），失敗一律轉 LLMError。

    同步阻塞函式——只能在 worker thread 內呼叫（embed_local 的 to_thread 包住），
    不得在事件迴圈執行緒直呼。
    """
    from app.llm import LLMError

    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:  # double-checked：等鎖期間別條 thread 可能已載完
            return _model
        return _load_model_locked(LLMError)


def _load_model_locked(LLMError):  # noqa: N803 - 例外類別參數
    global _model
    try:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        settings = get_settings()
        try:
            TextEmbedding.add_custom_model(
                model=LOCAL_EMBED_MODEL,
                pooling=PoolingType.CLS,
                normalization=True,
                sources=ModelSource(hf=LOCAL_EMBED_MODEL),
                dim=LOCAL_EMBED_DIM,
                model_file="onnx/model.onnx",
                additional_files=["onnx/model.onnx_data"],
                size_in_gb=2.2,
            )
        except ValueError:
            # 已註冊過（例如同進程重複呼叫、或未來 reset_for_tests 之外的路徑）；
            # fastembed 對重複註冊同名模型丟 ValueError，此處視為冪等、非錯誤。
            pass
        _model = TextEmbedding(
            model_name=LOCAL_EMBED_MODEL,
            cache_dir=settings.embed_cache_dir,
            threads=8,
        )
    except Exception as e:
        raise LLMError(f"本地 embedding 模型載入失敗：{e}") from e
    return _model


async def embed_local(texts: list[str], input_type: str) -> list[list[float]]:
    """本地推論入口。`input_type` 保留供未來換模型使用——BGE-M3 為對稱模型，
    passage/query 皆免前綴、走同一路徑（docs/02 D12）。

    走 `asyncio.to_thread` 避免阻塞事件迴圈——**含首次模型載入**（2.2GB 下載 +
    ~39s warmup 也在 worker thread 內，M14 審查 M1 修正）；批次大小固定 4
    （實測峰值 RAM 最省，見 M14-smoke-test-record §4）。
    """
    from app.llm import LLMError

    def _run() -> list[list[float]]:
        model = _get_model()  # 首載/下載都在 worker thread，事件迴圈不卡
        # 逐值轉 Python float：fastembed 回 numpy float32，直接 list() 會是
        # np.float32 清單，下游 json.dumps（update_chunk_embeddings）序列化直接炸
        # ——單元測試 mock 回 Python float 抓不到，真環境 E2E 才炸（M14 T-M14-99）。
        return [[float(x) for x in vec] for vec in model.embed(texts, batch_size=_BATCH_SIZE)]

    vectors = await asyncio.to_thread(_run)
    for vec in vectors:
        if len(vec) != LOCAL_EMBED_DIM:
            raise LLMError(f"本地 embedding 維度異常：預期 {LOCAL_EMBED_DIM}，實得 {len(vec)}")
    return vectors


def reset_for_tests() -> None:
    """測試用：清掉模組級單例，逼下次呼叫重新走 _get_model()。"""
    global _model
    _model = None
