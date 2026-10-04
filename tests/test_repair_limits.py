"""Assistant-authored mechanical tests for SQL repair attempt accounting."""

import pytest

from src.ecommerce_agent.repair_limits import (
    AttemptCounter,
    RepairLimitReached,
    RepairLimits,
)


def test_transport_retries_do_not_inflate_sql_or_repair_counts():
    counter = AttemptCounter(RepairLimits(max_repair_attempts=2))
    counter.record_initial_sql(entered_sqlite=True)
    counter.begin_repair()

    counter.record_model_http_attempt()
    counter.record_model_http_attempt()
    counter.record_model_http_attempt()
    before_candidate = counter.snapshot()

    assert before_candidate.repair_attempts == 1
    assert before_candidate.model_http_attempts == 3
    assert before_candidate.sql_attempts == 1

    counter.record_repair_candidate(entered_sqlite=False)
    final = counter.snapshot()

    assert final.repair_attempts == 1
    assert final.model_http_attempts == 3
    assert final.sql_attempts == 2
    assert final.sqlite_entries == 1


def test_repair_limit_implies_total_sql_attempt_limit():
    limits = RepairLimits(max_repair_attempts=2)
    counter = AttemptCounter(limits)
    counter.record_initial_sql(entered_sqlite=True)

    assert limits.max_total_sql_attempts == 3
    assert counter.begin_repair() == 1
    assert counter.record_repair_candidate(entered_sqlite=True) == 2
    assert counter.begin_repair() == 2
    assert counter.record_repair_candidate(entered_sqlite=True) == 3

    with pytest.raises(RepairLimitReached, match="最大 SQL 修复次数"):
        counter.begin_repair()


def test_transport_exhaustion_consumes_one_logical_repair_only():
    counter = AttemptCounter(RepairLimits(max_repair_attempts=2))
    counter.record_initial_sql(entered_sqlite=True)
    counter.begin_repair()
    for _ in range(3):
        counter.record_model_http_attempt()
    counter.end_repair_without_candidate()

    snapshot = counter.snapshot()
    assert snapshot.repair_attempts == 1
    assert snapshot.model_http_attempts == 3
    assert snapshot.sql_attempts == 1
    assert snapshot.repair_candidate_pending is False


@pytest.mark.parametrize("invalid", [-1, 1.5, True])
def test_repair_limit_must_be_a_nonnegative_integer(invalid):
    with pytest.raises(ValueError, match="非负整数"):
        RepairLimits(max_repair_attempts=invalid)


def test_counter_rejects_out_of_order_events():
    counter = AttemptCounter(RepairLimits(max_repair_attempts=1))

    with pytest.raises(RuntimeError, match="首次 SQL"):
        counter.begin_repair()
    with pytest.raises(RuntimeError, match="修复轮次"):
        counter.record_model_http_attempt()
    with pytest.raises(RuntimeError, match="修复候选"):
        counter.record_repair_candidate(entered_sqlite=False)

    counter.record_initial_sql(entered_sqlite=False)
    with pytest.raises(RuntimeError, match="首次 SQL"):
        counter.record_initial_sql(entered_sqlite=False)
