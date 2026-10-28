from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["once", "recurring", "interval"]
MAX_SCHEDULES_PER_PERSON = 20
MAX_RUNS_PER_DAY = 48
MAX_FAILURES_IN_A_ROW = 3
DEFAULT_APPROVAL_SECONDS = 12 * 3600


class ScheduleIn(BaseModel):
    agent_id: str
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=2000)
    kind: Kind = "recurring"
    cron_expression: str = Field(default="", max_length=100)
    """For `recurring`: 5 parts, minute hour day-of-month month day-of-week, like `0 8 * * 1-5`."""
    interval_seconds: int = Field(default=0, ge=0, le=7 * 24 * 3600)
    """For `interval`: at least 15 minutes."""
    run_at: float | None = None
    """For `once`: when, as epoch seconds."""
    timezone: str | None = None
    """An IANA name like `Europe/London`; the person's own when left out."""
    quiet: bool = False
    """Only tell the person when there is something to report."""
    email_results: bool = False
    approval_timeout_s: int = Field(default=DEFAULT_APPROVAL_SECONDS, ge=60, le=7 * 24 * 3600)


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: Kind | None = None
    cron_expression: str | None = Field(default=None, max_length=100)
    interval_seconds: int | None = Field(default=None, ge=0, le=7 * 24 * 3600)
    run_at: float | None = None
    timezone: str | None = None
    quiet: bool | None = None
    email_results: bool | None = None
    status: Literal["active", "paused"] | None = None


class FiringOut(BaseModel):
    id: str
    due_at: float
    zone: str = "UTC"
    """The time zone the schedule was in when this run was due."""
    status: str
    """pending, running, succeeded, failed, skipped or missed."""
    attempt: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    summary: str = ""
    error: str | None = None


class ScheduleOut(BaseModel):
    id: str
    agent_id: str
    agent_name: str = ""
    name: str
    prompt: str
    kind: Kind
    cron_expression: str
    interval_seconds: int
    run_at: float | None
    timezone: str
    timing: str
    """The timing in plain words."""
    status: Literal["active", "paused", "completed"]
    quiet: bool
    email_results: bool
    approval_timeout_s: int
    next_run_at: float | None
    last_run_at: float | None
    created_at: float
    updated_at: float
    recent: list[FiringOut] = Field(default_factory=list)


class ScheduleList(BaseModel):
    items: list[ScheduleOut]


class FiringList(BaseModel):
    items: list[FiringOut]
