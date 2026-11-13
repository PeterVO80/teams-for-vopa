"""ORM models, grouped per module.

Importing this package registers every model with SQLAlchemy's metadata,
which Flask-Migrate needs to autogenerate migrations.
"""

from .article import Article
from .base import LabeledEnum
from .knowledge import Note, Tag, WikiPage, WikiRevision, note_tags
from .planning import Assignment, AssignmentKind
from .project import Project, ProjectStatus
from .purchasing import GoodsReceipt, OrderStatus, PurchaseOrder, PurchaseOrderLine
from .task import OPEN_STATUSES, Task, TaskPriority, TaskStatus
from .user import User, UserRole

__all__ = [
    "Article",
    "Assignment",
    "AssignmentKind",
    "GoodsReceipt",
    "LabeledEnum",
    "Note",
    "OPEN_STATUSES",
    "OrderStatus",
    "Project",
    "ProjectStatus",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Tag",
    "Task",
    "TaskPriority",
    "TaskStatus",
    "User",
    "UserRole",
    "WikiPage",
    "WikiRevision",
    "note_tags",
]
