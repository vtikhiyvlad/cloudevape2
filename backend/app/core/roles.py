"""Роли и проверка прав — единый источник правды."""

OWNER = "owner"
ADMIN = "admin"
SELLER = "seller"
WAREHOUSE = "warehouse"

ALL_ROLES = (OWNER, ADMIN, SELLER, WAREHOUSE)
ASSIGNABLE_ROLES = (SELLER, ADMIN, WAREHOUSE)  # при привязке к магазину

ROLE_LABELS = {
    OWNER: "Владелец",
    ADMIN: "Админ магазина",
    SELLER: "Продавец",
    WAREHOUSE: "Склад",
}


def can_manage_stores(role: str) -> bool:
    return role in (OWNER, ADMIN)


def can_manage_inventory_tasks(role: str) -> bool:
    return role in (OWNER, ADMIN)


def can_apply_inventory(role: str) -> bool:
    return role in (OWNER, ADMIN)


def can_view_warehouse(role: str) -> bool:
    return role in (OWNER, ADMIN, WAREHOUSE)


def can_sell(role: str) -> bool:
    return role in (OWNER, ADMIN, SELLER)


def normalize_assign_role(role: str) -> str:
    return role if role in ASSIGNABLE_ROLES else SELLER
