"""
Feedback report models and the vocabularies they share.

A report is what someone said (``FeedbackCreate``) plus what we did about it
(``status``, ``triage_notes``, ``severity``). The two halves are edited
separately: the reporter's words are never rewritten, only triaged.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# Astraeus packages, offered as suggestions in the admin form. ``component`` is
# free text — a consumer filing against their own app is not an error.
DEFAULT_COMPONENTS = [
    "astraeus-otel",
    "astraeus-portal",
    "docs",
    "astraeus-media",
    "starlette-chat",
    "starlette-cms",
    "starlette-cms-feedback",
    "starlette-cms-gateways",
    "starlette-editor",
]


class FeedbackKind(StrEnum):
    BUG = "bug"
    FEEDBACK = "feedback"
    IDEA = "idea"


# Severity and status are listed in order of meaning, not alphabetically: the
# admin form renders them in this order.
class FeedbackSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKING = "blocking"


class FeedbackStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    FIXED = "fixed"
    WONTFIX = "wontfix"


# Statuses that still need someone to act. Everything else is settled.
OPEN_STATUSES = frozenset({FeedbackStatus.IN_PROGRESS, FeedbackStatus.NEW, FeedbackStatus.TRIAGED})


class FeedbackCreate(BaseModel):
    """What a reporter supplies. ``kind``, ``title`` and ``description`` are required."""

    actual: str | None = None
    component: str | None = None
    description: str = Field(min_length=1)
    environment: str | None = None
    expected: str | None = None
    kind: FeedbackKind
    reporter: str | None = None
    severity: FeedbackSeverity | None = None
    steps_to_reproduce: str | None = None
    title: str = Field(min_length=1, max_length=200)


class FeedbackUpdate(BaseModel):
    """The triage fields — the only part of a report that changes after filing."""

    severity: FeedbackSeverity | None = None
    status: FeedbackStatus | None = None
    triage_notes: str | None = None


class FeedbackReport(FeedbackCreate):
    """A stored report: the reporter's words plus triage state."""

    created_at: str
    id: str
    status: FeedbackStatus = FeedbackStatus.NEW
    triage_notes: str | None = None
    updated_at: str
