"""Store AIHubMix balance snapshots locally and aggregate them into spend over time."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path


APP_DIR = "aihubmix-usage"
HISTORY_FILE = "history.jsonl"


def data_dir(override: Path | str | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    if os.environ.get("AIHUBMIX_USAGE_DIR"):
        return Path(os.environ["AIHUBMIX_USAGE_DIR"]).expanduser()
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]).expanduser() / APP_DIR
    return Path.home() / ".local" / "share" / APP_DIR


def history_path(base: Path | str | None = None) -> Path:
    return data_dir(base) / HISTORY_FILE


def load(path: Path) -> list[dict]:
    """Read snapshots oldest first, skipping any malformed line."""
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "ts" in item and "used" in item:
            entries.append(item)
    entries.sort(key=lambda item: item["ts"])
    return entries


def record(path: Path, snapshot: dict) -> dict:
    """Append one snapshot and return it with its wall-clock timestamp attached."""
    entry = {
        "ts": float(snapshot.get("ts") or datetime.datetime.now().timestamp()),
        "used": round(float(snapshot.get("used", 0.0)), 6),
        "remaining": round(float(snapshot.get("remaining", 0.0)), 6),
        "total": round(float(snapshot.get("total", 0.0)), 6),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _day(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def daily_spend(entries: list[dict], days: int | None = None) -> list[dict]:
    """Attribute the increase between consecutive snapshots to the later snapshot's day."""
    if len(entries) < 2:
        return []
    buckets: dict[str, float] = {}
    order: list[str] = []
    for previous, current in zip(entries, entries[1:]):
        delta = float(current["used"]) - float(previous["used"])
        if delta < 0:
            continue
        day = _day(float(current["ts"]))
        if day not in buckets:
            buckets[day] = 0.0
            order.append(day)
        buckets[day] += delta
    rows = [{"date": day, "spend": buckets[day]} for day in order]
    if days is not None and days > 0:
        cutoff = (
            datetime.datetime.now() - datetime.timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        rows = [row for row in rows if row["date"] >= cutoff]
    return rows


def span_days(entries: list[dict]) -> float:
    if len(entries) < 2:
        return 0.0
    seconds = float(entries[-1]["ts"]) - float(entries[0]["ts"])
    return max(seconds / 86400.0, 0.0)


def burn_rate(entries: list[dict], days: int | None = None) -> float:
    """Average US dollars spent per day over the tracked window."""
    if len(entries) < 2:
        return 0.0
    window = entries
    if days is not None and days > 0:
        cutoff = (
            datetime.datetime.now() - datetime.timedelta(days=days)
        ).timestamp()
        window = [item for item in entries if float(item["ts"]) >= cutoff]
        if len(window) < 2:
            window = entries
    spent = float(window[-1]["used"]) - float(window[0]["used"])
    days_span = span_days(window)
    if days_span <= 0 or spent <= 0:
        return 0.0
    return spent / days_span


def summarize(entries: list[dict], days: int | None = None) -> dict:
    return {
        "snapshots": len(entries),
        "first": _day(float(entries[0]["ts"])) if entries else "",
        "last": _day(float(entries[-1]["ts"])) if entries else "",
        "span_days": round(span_days(entries), 2),
        "burn_per_day": round(burn_rate(entries, days), 4),
        "daily": daily_spend(entries, days),
    }
