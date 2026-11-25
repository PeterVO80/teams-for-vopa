"""ORM models, grouped per module.

Importing this package registers every model with SQLAlchemy's metadata,
which Flask-Migrate needs to autogenerate migrations.
"""

from .article import Article
from .base import LabeledEnum
from .catalog import CatalogItem, SupplierCatalog
from .company import Company, Contact
from .outlook import CalendarEvent, OutlookLink
from .knowledge import Note, Tag, WikiPage, WikiRevision, note_tags
from .planning import Assignment, AssignmentKind, is_weekend
from .setting import AppSetting
from .quote import PdfDetail, Quote, QuoteLine, QuoteLineKind, QuoteStatus, QuoteTextTemplate, ServiceType
from .timesheet import TimeEntry
from .login import LoginAttempt
from .project import Project, ProjectStatus, project_members
from .stock import MovementKind, StockLocation, StockMovement
from .purchasing import GoodsReceipt, OrderStatus, PurchaseOrder, PurchaseOrderLine
from .task import OPEN_STATUSES, Task, TaskPriority, TaskStatus
from .user import User, UserRole

__all__ = [
    "TimeEntry",
    "AppSetting",
    "Article",
    "Assignment",
    "CalendarEvent",
    "CatalogItem",
    "Company",
    "Contact",
    "AssignmentKind",
    "GoodsReceipt",
    "LabeledEnum",
    "LoginAttempt",
    "MovementKind",
    "Note",
    "OPEN_STATUSES",
    "OrderStatus",
    "OutlookLink",
    "Project",
    "StockLocation",
    "StockMovement",
    "SupplierCatalog",
    "ProjectStatus",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PdfDetail",
    "Quote",
    "QuoteLine",
    "QuoteLineKind",
    "QuoteStatus",
    "QuoteTextTemplate",
    "ServiceType",
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
    "project_members",
]
