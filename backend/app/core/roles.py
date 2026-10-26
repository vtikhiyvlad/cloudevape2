"""Роли — единый источник правды."""

SYSADMIN = "sysadmin"   # логин/пароль из env — техподдержка
OWNER = "owner"         # владелец бизнеса (Telegram)
ADMIN = "admin"         # админ магазина
SELLER = "seller"       # продавец
WAREHOUSE = "warehouse" # только товары/остатки

ALL_ROLES = (SYSADMIN, OWNER, ADMIN, SELLER, WAREHOUSE)
ASSIGNABLE_ROLES = (SELLER, ADMIN, WAREHOUSE, OWNER)

ROLE_LABELS = {
    SYSADMIN: "Сисадмин",
    OWNER: "Владелец",
    ADMIN: "Админ магазина",
    SELLER: "Продавец",
    WAREHOUSE: "Склад",
}


def can_manage_stores(role: str) -> bool:
    return role in (SYSADMIN, OWNER)


def can_manage_sellers(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN)


def can_manage_inventory_tasks(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN)


def can_apply_inventory(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN)


def can_view_sales(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN)


def can_view_warehouse(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN, WAREHOUSE)


def can_edit_products(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN, WAREHOUSE)


def can_sell(role: str) -> bool:
    return role in (SYSADMIN, OWNER, ADMIN, SELLER)


def is_staff_admin(role: str) -> bool:
    """Полная админка (не склад)."""
    return role in (SYSADMIN, OWNER, ADMIN)


def normalize_assign_role(role: str) -> str:
    if role in (SELLER, ADMIN, WAREHOUSE, OWNER):
        return role
    return SELLER
