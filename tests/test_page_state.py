"""Tests for rerun-safe User interface page state transitions."""

from src.ecommerce_agent.page_state import PagePhase, PageState


def test_page_state_starts_one_normalized_submission():
    state, submission = PageState[str]().begin("  分析月度 GMV。  ")

    assert submission is not None
    assert submission.question == "分析月度 GMV。"
    assert state.phase is PagePhase.SUBMITTING
    assert state.active_token == submission.token
    assert state.last_question == submission.question
    assert state.result is None


def test_blank_or_in_flight_rerun_does_not_start_another_submission():
    idle = PageState[str]()
    unchanged, blank = idle.begin("   ")
    submitting, first = idle.begin("分析订单量。")
    repeated, second = submitting.begin("分析订单量。")

    assert unchanged is idle
    assert blank is None
    assert first is not None
    assert repeated is submitting
    assert second is None


def test_only_matching_submission_can_replace_the_visible_result():
    initial = PageState[str](
        phase=PagePhase.COMPLETE,
        last_question="旧问题",
        result="旧结果",
    )
    submitting, current = initial.begin("新问题")
    _, stale = PageState[str]().begin("另一问题")
    assert current is not None and stale is not None

    ignored = submitting.finish(stale, "错误结果")
    completed = submitting.finish(current, "新结果")

    assert ignored is submitting
    assert completed.phase is PagePhase.COMPLETE
    assert completed.active_token is None
    assert completed.result == "新结果"
    assert completed.last_question == "新问题"
