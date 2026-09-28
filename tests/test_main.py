from datetime import UTC, datetime, timedelta

from src.main import FINISH_RESERVE, MIN_CRAWL_SHARE, int_input, plan_deadlines

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_plan_deadlines_without_timeout() -> None:
    assert plan_deadlines(NOW, None, ai_seconds=100) == (None, None)


def test_plan_deadlines_reserves_time_for_ai_and_saving() -> None:
    timeout_at = NOW + timedelta(hours=1)
    crawl_deadline, ai_deadline = plan_deadlines(NOW, timeout_at, ai_seconds=300)
    assert ai_deadline == timeout_at - FINISH_RESERVE
    assert crawl_deadline == ai_deadline - timedelta(seconds=300)


def test_plan_deadlines_keeps_most_of_a_short_run_for_the_crawl() -> None:
    """With a short timeout, the AI reserve can't eat the whole run and leave nothing to curate."""
    timeout_at = NOW + FINISH_RESERVE + timedelta(seconds=100)
    crawl_deadline, ai_deadline = plan_deadlines(NOW, timeout_at, ai_seconds=1000)
    assert crawl_deadline == NOW + timedelta(seconds=100 * MIN_CRAWL_SHARE)
    assert ai_deadline == NOW + timedelta(seconds=100)


def test_plan_deadlines_without_ai() -> None:
    timeout_at = NOW + timedelta(minutes=10)
    crawl_deadline, ai_deadline = plan_deadlines(NOW, timeout_at, ai_seconds=0)
    assert crawl_deadline == ai_deadline == timeout_at - FINISH_RESERVE


def test_int_input() -> None:
    assert int_input(None, 100, 2, 5000) == 100
    assert int_input('abc', 100, 2, 5000) == 100
    assert int_input('20', 100, 2, 5000) == 20
    assert int_input(0, 100, 2, 5000) == 2
    assert int_input(10**9, 100, 2, 5000) == 5000
