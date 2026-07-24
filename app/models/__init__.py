"""Все модели в одном месте — чтобы Alembic видел метаданные целиком."""
from app.models.content import Article, TrackingLink
from app.models.project import Case, Example, Keyword, Project, VoiceProfile
from app.models.tenancy import Tenant, User
from app.models.tracking import Deal, JobRun, MetricsSnapshot, Subscriber

__all__ = [
    "Article",
    "Case",
    "Deal",
    "Example",
    "JobRun",
    "Keyword",
    "MetricsSnapshot",
    "Project",
    "Subscriber",
    "Tenant",
    "TrackingLink",
    "User",
    "VoiceProfile",
]
