"""The single place a metrics source plugs in — mirrors publishers/registry.py.

Add a platform's stats by writing `fetch(video_ids) -> {video_id: {metric: value}}` and
registering a `StatsProvider`. The poller and dashboard pick it up automatically. Metric keys the
DB has columns for (views, likes, comments, shares, watch_time_minutes, avg_view_seconds,
avg_view_pct, impressions, ctr) are stored directly; anything else is kept in extra_json.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# video_id -> {metric_name: value}
FetchFn = Callable[[list[str]], dict[str, dict]]


@dataclass(frozen=True)
class StatsProvider:
    platform: str
    fetch: FetchFn


_REGISTRY: dict[str, StatsProvider] = {}


def register(provider: StatsProvider) -> None:
    _REGISTRY[provider.platform] = provider


def get(platform: str) -> StatsProvider | None:
    return _REGISTRY.get(platform)


def platforms() -> list[str]:
    return list(_REGISTRY)
