"""FastAPI 共用 dependencies。

`require_json_content_type`（M15 T-FD-04 / 安全批次）：對「無 body 的 state-changing POST」
做最小 CSRF 防護。本專案 API 無認證，安全模型倚賴網路邊界（見 docs/02-architecture.md
部署假設）。跨站 HTML `<form>` 只能送 simple content-type（`application/x-www-form-urlencoded`
/ `multipart/form-data` / `text/plain`），無法設 `application/json`——設了會觸發 CORS
preflight，而本專案未開 CORS → 被瀏覽器擋。因此要求這些端點帶 `Content-Type: application/json`
即可關閉「惡意網頁以簡單表單 POST 打中無認證、無 body 端點」的攻擊面。缺或不符回 400
`json_required`。

不套在檔案上傳端點（`POST /api/documents` 走 multipart，且跨站上傳合法 PDF 不切實際）。
DELETE/PATCH/PUT 為非 simple method，瀏覽器強制 preflight、CORS 未開即被擋，本就無需此防護。
"""

from fastapi import Request

from app.errors import AppError


async def require_json_content_type(request: Request) -> None:
    """要求請求帶 `Content-Type: application/json`；否則回 400 `json_required`。"""
    # 只比對 media type 主體，容許參數（如 "application/json; charset=utf-8"）
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise AppError(
            "json_required",
            "此端點要求 Content-Type: application/json",
            status=400,
        )
