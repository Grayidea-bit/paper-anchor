"""維護動作端點（M14 D12 / T-EM-02）。

薄層：業務邏輯全委派 `services/reembed.py`；與 backup/restore 共用同一把服務層鎖
（`services/backup.py`），三方互斥。錯誤走既有 `AppError` handler（見 app/main.py）。
"""

from fastapi import APIRouter, BackgroundTasks, Depends

from app.deps import require_json_content_type
from app.errors import AppError
from app.services import backup, reembed

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.post("/reembed", status_code=202, dependencies=[Depends(require_json_content_type)])
async def trigger_reembed(background_tasks: BackgroundTasks) -> dict:
    """全庫向量重建（切換 embed 來源後重嵌，見 D12）。

    backup/restore/reembed 任一進行中回 409 `operation_running`（同 `routers/backup.py`
    的 `POST /restore` 語意，三方共用 `backup.is_running()`）。
    """
    if backup.is_running():
        raise AppError("operation_running", "已有備份、還原或重嵌進行中", status=409)
    background_tasks.add_task(reembed.run_reembed)
    return {"started": True}
