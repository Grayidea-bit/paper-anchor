from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import repo
from app.db.session import SessionLocal
from app.errors import NotFoundError

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ConversationCreate(BaseModel):
    title: str = "新對話"


@router.get("")
async def list_projects() -> list[dict]:
    async with SessionLocal() as session:
        return await repo.list_projects(session)


@router.post("", status_code=201)
async def create_project(body: ProjectBody) -> dict:
    async with SessionLocal() as session:
        return await repo.create_project(session, body.name.strip())


@router.patch("/{project_id}")
async def rename_project(project_id: int, body: ProjectBody) -> dict:
    async with SessionLocal() as session:
        project = await repo.rename_project(session, project_id, body.name.strip())
    if project is None:
        raise NotFoundError("project", project_id)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int) -> None:
    async with SessionLocal() as session:
        if not await repo.delete_project(session, project_id):
            raise NotFoundError("project", project_id)


@router.get("/{project_id}/conversations")
async def list_project_conversations(project_id: int) -> list[dict]:
    async with SessionLocal() as session:
        if await repo.get_project(session, project_id) is None:
            raise NotFoundError("project", project_id)
        return await repo.list_conversations_scoped(session, scope="project", project_id=project_id)


@router.post("/{project_id}/conversations", status_code=201)
async def create_project_conversation(project_id: int, body: ConversationCreate) -> dict:
    async with SessionLocal() as session:
        if await repo.get_project(session, project_id) is None:
            raise NotFoundError("project", project_id)
        return await repo.create_conversation(
            session, scope="project", title=body.title, project_id=project_id
        )
