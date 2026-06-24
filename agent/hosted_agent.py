import os

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from app.config import get_settings
from app.menu_models import MenuSearchCriteria
from app.menu_repository import create_menu_repository

INSTRUCTIONS = """
あなたは夕食献立サジェスト AI エージェントです。
登録済みメニューを第一候補として扱い、通常の献立推薦では Web 検索を使いません。
ユーザーが新しいメニューや未登録候補を明示した場合だけ Web 検索の利用を検討します。
材料リストを求められた場合は、必ず find_menus か get_menus で取得した材料をもとに答えてください。
料理名や menu id が分かっている場合は、まず find_menus にその文字列を渡してください。
""".strip()

settings = get_settings()
repository = create_menu_repository(settings)


@tool(approval_mode="never_require")
def search_menus(
    main_ingredient: str | None = None,
    category: str | None = None,
    cook_with: str | None = None,
    max_items: int = 10,
) -> list[dict[str, object]]:
    criteria = MenuSearchCriteria(
        main_ingredient=main_ingredient,
        category=category,
        cook_with=cook_with,
        max_items=min(max(max_items, 1), 20),
    )
    return [menu.model_dump(mode="json") for menu in repository.search_menus(criteria)]


@tool(approval_mode="never_require")
def get_menus(menu_ids: list[str]) -> list[dict[str, object]]:
    menus = repository.get_menus(menu_ids)
    found_ids = {menu.id for menu in menus}
    unresolved = [menu_id for menu_id in menu_ids if menu_id not in found_ids]
    if unresolved:
        all_menus = repository.search_menus(MenuSearchCriteria(max_items=20))
        for requested_menu in unresolved:
            menus.extend(
                menu
                for menu in all_menus
                if requested_menu == menu.name
                or requested_menu in menu.name
                or requested_menu == menu.id
            )
    return [menu.model_dump(mode="json") for menu in menus]


@tool(approval_mode="never_require")
def find_menus(query: str, max_items: int = 10) -> list[dict[str, object]]:
    all_menus = repository.search_menus(MenuSearchCriteria(max_items=20))
    normalized_query = query.strip().lower()
    matched_menus = [
        menu
        for menu in all_menus
        if normalized_query in menu.id.lower()
        or menu.id.lower() in normalized_query
        or normalized_query in menu.name.lower()
        or menu.name.lower() in normalized_query
    ]
    if matched_menus:
        return [menu.model_dump(mode="json") for menu in matched_menus[:max_items]]

    for menu in all_menus:
        searchable_values = [
            menu.main_ingredient,
            menu.category,
            menu.cook_with,
            *(ingredient.name for ingredient in menu.ingredients),
        ]
        if any(normalized_query in value.lower() for value in searchable_values):
            matched_menus.append(menu)
    return [menu.model_dump(mode="json") for menu in matched_menus[:max_items]]


def create_agent() -> Agent:
    client = FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini"),
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client=client,
        name="ai-dinner-planner",
        instructions=INSTRUCTIONS,
        tools=[search_menus, get_menus, find_menus],
        default_options={"store": False},
    )
