from fastapi import APIRouter

from app import tools

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def get_tools() -> list[dict]:
    """已註冊的 LLM 工具（唯讀；新增方式：複製 backend/app/tools/template_tool.py）。"""
    return tools.list_tools()
