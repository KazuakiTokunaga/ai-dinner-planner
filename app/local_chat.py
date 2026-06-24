from dataclasses import dataclass

from app.menu_models import Menu, MenuSearchCriteria
from app.menu_repository import MenuRepository

INGREDIENT_HINTS = ["鶏肉", "豚肉", "牛肉", "海鮮", "魚", "野菜"]
CATEGORY_HINTS = ["カレー・シチュー", "鍋", "パスタ", "丼", "炒め物", "煮物"]
COOK_WITH_HINTS = ["ホットクック", "フライパン", "鍋", "オーブン"]


@dataclass(frozen=True)
class LocalChatResult:
    message: str
    context_menu_ids: list[str]


class LocalDinnerChatService:
    def __init__(self, repository: MenuRepository, default_max_items: int) -> None:
        self._repository = repository
        self._default_max_items = default_max_items

    def respond(self, message: str, context_menu_ids: list[str] | None = None) -> LocalChatResult:
        if self._asks_for_ingredients(message):
            menus = self._find_requested_menus(message)
            if not menus and context_menu_ids:
                menus = self._repository.get_menus(context_menu_ids)

            if menus:
                return LocalChatResult(
                    message=self._format_ingredients(menus),
                    context_menu_ids=[menu.id for menu in menus],
                )

            return LocalChatResult(
                message="材料リストを出すメニュー ID またはメニュー名を指定してください。",
                context_menu_ids=context_menu_ids or [],
            )

        criteria = MenuSearchCriteria(
            main_ingredient=self._find_hint(message, INGREDIENT_HINTS),
            category=self._find_hint(message, CATEGORY_HINTS),
            cook_with=self._find_hint(message, COOK_WITH_HINTS),
            max_items=self._default_max_items,
        )
        menus = self._repository.search_menus(criteria)
        if not menus:
            return LocalChatResult(
                message=(
                    "登録済みメニューでは候補が見つかりませんでした。"
                    "新しいメニューも探す場合は、その旨を明示してください。"
                ),
                context_menu_ids=context_menu_ids or [],
            )

        return LocalChatResult(
            message=self._format_recommendations(menus),
            context_menu_ids=[menu.id for menu in menus],
        )

    @staticmethod
    def _find_hint(message: str, hints: list[str]) -> str | None:
        return next((hint for hint in hints if hint in message), None)

    def _find_requested_menus(self, message: str) -> list[Menu]:
        all_menus = self._repository.search_menus(
            MenuSearchCriteria(max_items=self._default_max_items)
        )
        requested_ids = [menu.id for menu in all_menus if menu.id in message]
        requested_ids.extend(menu.id for menu in all_menus if menu.name in message)

        if not requested_ids:
            return []

        unique_ids = list(dict.fromkeys(requested_ids))
        return self._repository.get_menus(unique_ids)

    @staticmethod
    def _asks_for_ingredients(message: str) -> bool:
        return "材料" in message or "買い物" in message or "食材" in message

    @staticmethod
    def _format_recommendations(menus: list[Menu]) -> str:
        lines = ["登録済みメニューから候補を出します。"]
        for menu in menus:
            lines.append(
                f"- {menu.name}"
                f"（ID: {menu.id}、主材料: {menu.main_ingredient}、調理: {menu.cook_with}）"
            )
        lines.append("材料リストが必要なメニュー ID を指定してください。")
        return "\n".join(lines)

    @staticmethod
    def _format_ingredients(menus: list[Menu]) -> str:
        if not menus:
            return "指定されたメニュー ID は登録済みメニューから見つかりませんでした。"

        lines = ["材料リストです。"]
        for menu in menus:
            lines.append(f"\n{menu.name}（{menu.id}）")
            for ingredient in menu.ingredients:
                lines.append(f"- {ingredient.name}: {ingredient.quantity}")
        return "\n".join(lines)
