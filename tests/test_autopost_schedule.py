"""Posting-time experiment: explore mode's daily slot rotation and its guardrails."""
from datetime import date, timedelta

from clipfarm.autopost import AutopostSettings, effective_post_hours, explore_hours_for


def _settings(**overrides):
    return AutopostSettings(**overrides).normalized()


def test_explore_hours_deterministic_within_a_day():
    s = _settings(schedule_mode="explore")
    day = date(2026, 7, 13)
    # The scheduler re-derives the slots on every tick; they must not move during the day.
    assert explore_hours_for(s, day) == explore_hours_for(s, day)


def test_explore_hours_stay_in_window_and_keep_slot_count():
    s = _settings(schedule_mode="explore", post_hours=[12, 17, 21], explore_window=[11, 23])
    for offset in range(30):
        hours = explore_hours_for(s, date(2026, 7, 1) + timedelta(days=offset))
        assert len(hours) == 3
        assert all(11 <= h <= 23 for h in hours)
        assert hours == sorted(hours)  # spread across the day, not clumped


def test_explore_rotation_covers_the_whole_window():
    # The entire point: over a couple of weeks, every hour in the window gets sampled so
    # hour_performance() learns about all of them, not just 3.
    s = _settings(schedule_mode="explore", post_hours=[12, 17, 21], explore_window=[11, 23])
    sampled: set[int] = set()
    for offset in range(14):
        sampled.update(explore_hours_for(s, date(2026, 7, 1) + timedelta(days=offset)))
    assert sampled == set(range(11, 24))


def test_explore_hours_differ_across_days():
    s = _settings(schedule_mode="explore")
    day = date(2026, 7, 13)
    assert explore_hours_for(s, day) != explore_hours_for(s, day + timedelta(days=1))


def test_effective_hours_dispatches_by_mode():
    day = date(2026, 7, 13)
    fixed = _settings(schedule_mode="fixed", post_hours=[12, 17, 21])
    assert effective_post_hours(fixed, day) == [12, 17, 21]
    explore = _settings(schedule_mode="explore", post_hours=[12, 17, 21])
    assert effective_post_hours(explore, day) == explore_hours_for(explore, day)


def test_normalized_rejects_bad_mode_and_window():
    s = AutopostSettings(schedule_mode="yolo", explore_window=[23, 23]).normalized()
    assert s.schedule_mode == "fixed"
    assert s.explore_window == [11, 23]  # degenerate window falls back to the default


def test_more_slots_than_window_hours_is_clamped():
    s = _settings(schedule_mode="explore", post_hours=[1, 2, 3, 4, 5], explore_window=[20, 22])
    hours = explore_hours_for(s, date(2026, 7, 13))
    assert len(hours) == 3  # can't schedule 5 posts inside a 3-hour window
    assert all(20 <= h <= 22 for h in hours)
