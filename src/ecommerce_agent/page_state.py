"""Small, framework-neutral page state for the User interface Streamlit UI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Generic, TypeVar
from uuid import uuid4


ResultT = TypeVar("ResultT")


class PagePhase(str, Enum):
    IDLE = "idle"
    SUBMITTING = "submitting"
    COMPLETE = "complete"


@dataclass(frozen=True)
class Submission:
    token: str
    question: str


@dataclass(frozen=True)
class PageState(Generic[ResultT]):
    """Only user-visible, rerun-safe state retained by the page."""

    phase: PagePhase = PagePhase.IDLE
    active_token: str | None = None
    last_question: str | None = None
    result: ResultT | None = None

    def begin(self, question: str) -> tuple["PageState[ResultT]", Submission | None]:
        normalized = question.strip()
        if not normalized or self.phase is PagePhase.SUBMITTING:
            return self, None
        submission = Submission(token=uuid4().hex, question=normalized)
        return (
            replace(
                self,
                phase=PagePhase.SUBMITTING,
                active_token=submission.token,
                last_question=normalized,
            ),
            submission,
        )

    def finish(self, submission: Submission, result: ResultT) -> "PageState[ResultT]":
        if (
            self.phase is not PagePhase.SUBMITTING
            or self.active_token != submission.token
        ):
            return self
        return replace(
            self,
            phase=PagePhase.COMPLETE,
            active_token=None,
            result=result,
        )

