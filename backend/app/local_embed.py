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

from app.config import get_settings

LOCAL_EMBED_MODEL = "BAAI/bge-m3"
LOCAL_EMBED_DIM = 1024

_BATCH_SIZE = 4

_model = None  # 模組級單例（懶載入）


def _get_model():
    """懶載入單例：首次呼叫才註冊 + 載入模型（含下載），失敗一律轉 LLMError。"""
    from app.llm import LLMError

    global _model
    if _model is not None:
        return _model
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

    走 `asyncio.to_thread` 避免阻塞事件迴圈；批次大小固定 4（實測峰值 RAM 最省，
    見 M14-smoke-test-record §4）。
    """
    from app.llm import LLMError

    model = _get_model()

    def _run() -> list[list[float]]:
        return [list(vec) for vec in model.embed(texts, batch_size=_BATCH_SIZE)]

    vectors = await asyncio.to_thread(_run)
    for vec in vectors:
        if len(vec) != LOCAL_EMBED_DIM:
            raise LLMError(f"本地 embedding 維度異常：預期 {LOCAL_EMBED_DIM}，實得 {len(vec)}")
    return vectors


def reset_for_tests() -> None:
    """測試用：清掉模組級單例，逼下次呼叫重新走 _get_model()。"""
    global _model
    _model = None
