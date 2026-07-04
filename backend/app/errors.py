class AppError(Exception):
    """業務錯誤，由 main.py 的 handler 統一轉成 {"error": {"code", "message"}}。"""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: int) -> None:
        super().__init__("not_found", f"{resource} {resource_id} 不存在", status=404)
