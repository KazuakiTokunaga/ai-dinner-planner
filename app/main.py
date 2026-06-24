from functools import lru_cache
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.foundry_client import FoundryHostedAgentClient
from app.local_chat import LocalDinnerChatService
from app.menu_models import ChatRequest, ChatResponse, Menu, MenuSearchCriteria
from app.menu_repository import MenuRepository, create_menu_repository

app = FastAPI(title="AI Dinner Planner")
app.mount("/static", StaticFiles(directory="static"), name="static")
local_chat_contexts: dict[str, list[str]] = {}


@lru_cache
def _settings() -> Settings:
    return get_settings()


@lru_cache
def _repository() -> MenuRepository:
    return create_menu_repository(_settings())


def settings_dependency() -> Settings:
    return _settings()


def repository_dependency() -> MenuRepository:
    return _repository()


SettingsDependency = Depends(settings_dependency)
RepositoryDependency = Depends(repository_dependency)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/healthz")
def healthz(settings: Settings = SettingsDependency) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "environment": settings.app_env,
        "use_cosmos": settings.use_cosmos,
    }


@app.get("/api/menus/search")
def search_menus(
    main_ingredient: str | None = None,
    category: str | None = None,
    cook_with: str | None = None,
    max_items: int = 10,
    repository: MenuRepository = RepositoryDependency,
) -> list[Menu]:
    criteria = MenuSearchCriteria(
        main_ingredient=main_ingredient,
        category=category,
        cook_with=cook_with,
        max_items=max_items,
    )
    return repository.search_menus(criteria)


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    settings: Settings = SettingsDependency,
    repository: MenuRepository = RepositoryDependency,
) -> ChatResponse:
    foundry_client = FoundryHostedAgentClient(settings)
    if foundry_client.is_configured:
        message, thread_id = await foundry_client.get_response(
            request.message,
            request.thread_id,
        )
        return ChatResponse(message=message, thread_id=thread_id, source="foundry")

    local_chat = LocalDinnerChatService(repository, settings.max_search_items)
    thread_id = request.thread_id or str(uuid4())
    result = local_chat.respond(
        request.message,
        context_menu_ids=local_chat_contexts.get(thread_id),
    )
    local_chat_contexts[thread_id] = result.context_menu_ids

    return ChatResponse(
        message=result.message,
        thread_id=thread_id,
        source="local",
    )
