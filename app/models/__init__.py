"""ORM models, grouped per module.

Importing this package registers every model with SQLAlchemy's metadata,
which Flask-Migrate needs to autogenerate migrations.
"""

from .article import Article
from .base import LabeledEnum
from .catalog import CatalogItem, SupplierCatalog
from .knowledge import Note, Tag, WikiPage, WikiRevision, note_tags
from .planning import Assignment, AssignmentKind, is_weekend
from .project import Project, ProjectStatus
from .stock import MovementKind, StockLocation, StockMovement
from .purchasing import GoodsReceipt, OrderStatus, PurchaseOrder, PurchaseOrderLine
from .task import OPEN_STATUSES, Task, TaskPriority, TaskStatus
from .user import User, UserRole

__all__ = [
    "Article",
    "Assignment",
    "CatalogItem",
    "AssignmentKind",
    "GoodsReceipt",
    "LabeledEnum",
    "MovementKind",
    "Note",
    "OPEN_STATUSES",
    "OrderStatus",
    "Project",
    "StockLocation",
    "StockMovement",
    "SupplierCatalog",
    "ProjectStatus",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Tag",
    "is_weekend",
    "Task",
    "TaskPriority",
    "TaskStatus",
    "User",
    "UserRole",
    "WikiPage",
    "WikiRevision",
    "note_tags",
]
