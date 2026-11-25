"""The modules of the application and the access levels per user.

Every user has, per module, no access, read access or write access (write
includes read). Administrators always have write access everywhere. The
levels are stored on the user as text, e.g. ``"projects:w,tasks:w,stock:r"``
(``User.module_access``); a module that isn't listed means no access.

This file has no imports from the app, so models and blueprints can both use it.
"""

from __future__ import annotations

READ, WRITE = "r", "w"

#: (key, label) in menu order.
MODULES: list[tuple[str, str]] = [
    ("projects", "Projecten"),
    ("quotes", "Offertes"),
    ("tasks", "Punchlijst (taken)"),
    ("team", "Team (planning)"),
    ("timesheet", "Uren"),
    ("purchasing", "Bestellingen"),
    ("articles", "Artikelen & catalogi"),
    ("stock", "Stock"),
    ("relations", "Relaties"),
    ("notes", "Notities"),
    ("wiki", "Wiki"),
]
MODULE_KEYS = [key for key, _ in MODULES]

#: Blueprint -> module. Blueprints not listed (dashboard, login, user
#: management, back-ups) are not part of the per-module access.
BLUEPRINT_MODULE = {
    "projects": "projects",
    "quotes": "quotes",
    "tasks": "tasks",
    "team": "team",
    "timesheet": "timesheet",
    "purchasing": "purchasing",
    "articles": "articles",
    "catalog": "articles",
    "stock": "stock",
    "relations": "relations",
    "notes": "notes",
    "wiki": "wiki",
}


def parse_access(text: str | None) -> dict[str, str]:
    """``"projects:w,stock:r"`` -> ``{"projects": "w", "stock": "r"}`` (unknown parts ignored)."""
    result = {}
    for part in (text or "").split(","):
        key, _, level = part.strip().partition(":")
        if key in MODULE_KEYS and level in (READ, WRITE):
            result[key] = level
    return result


def dump_access(levels: dict[str, str]) -> str:
    return ",".join(f"{key}:{levels[key]}" for key in MODULE_KEYS if levels.get(key) in (READ, WRITE))


#: New users (and all users that existed before access per module): write everywhere.
#: (Modules added later, such as quotes, were not given to existing users:
#: see the migration that added them.)
FULL_ACCESS = dump_access({key: WRITE for key in MODULE_KEYS})
