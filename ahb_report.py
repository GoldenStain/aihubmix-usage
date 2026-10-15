"""Build and render the AIHubMix usage, balance, key, and history report."""

from __future__ import annotations

import datetime

import ahb_api
import ahb_history
import ahb_render as render


MODEL_LIMIT = 50


def redact(token: str) -> str:
    """Show only a short prefix and suffix of a key, never the whole secret."""
    if not token:
        return "(none)"
    if len(token) <= 12:
        return token[:3] + "…"
    return f"{token[:6]}…{token[-4:]}"


def _severity(remaining_pct, warn_pct: float, crit_pct: float) -> str:
    if remaining_pct is None:
        return "cyan"
    if remaining_pct <= crit_pct:
        return "red"
    if remaining_pct <= warn_pct:
        return "yellow"
    return "green"


def build_report(
    client: ahb_api.Client,
    *,
    manage_key: str = "",
    include_models: bool = False,
    days: int | None = None,
    warn_pct: float = 20.0,
    crit_pct: float = 10.0,
    history_path=None,
) -> dict:
    """Collect balance, system, key, and model data; each endpoint may fail on its own."""
    errors: list[str] = []
    report = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base": client.base,
        "key_hint": redact(client.key),
        "errors": errors,
        "system": {},
        "quota_per_unit": 500000,
        "balance": {
            "total": None,
            "used": None,
            "remaining": None,
            "used_pct": None,
            "remaining_pct": None,
            "lifetime_usage_usd": None,
            "soft_limit_usd": None,
            "hard_limit_usd": None,
            "system_hard_limit_usd": None,
            "has_payment_method": None,
            "access_until": None,
        },
        "model_count": 0,
        "models": [],
        "keys": [],
        "history": {"present": False, "snapshots": 0},
        "warn_pct": warn_pct,
        "crit_pct": crit_pct,
    }
    balance = report["balance"]

    try:
        system = client.system_status()
        report["system"] = system
        report["quota_per_unit"] = int(system.get("quota_per_unit") or 500000)
    except ahb_api.ApiError as error:
        errors.append(f"status: {error}")

    subscription = {}
    try:
        subscription = client.subscription()
        balance["hard_limit_usd"] = _opt_float(subscription.get("hard_limit_usd"))
        balance["soft_limit_usd"] = _opt_float(subscription.get("soft_limit_usd"))
        balance["system_hard_limit_usd"] = _opt_float(subscription.get("system_hard_limit_usd"))
        balance["has_payment_method"] = subscription.get("has_payment_method")
        balance["access_until"] = subscription.get("access_until")
        balance["total"] = (
            balance["hard_limit_usd"]
            or balance["system_hard_limit_usd"]
            or balance["soft_limit_usd"]
        )
    except ahb_api.ApiError as error:
        errors.append(f"subscription: {error}")

    try:
        balance["remaining"] = client.remain()
    except ahb_api.ApiError as error:
        errors.append(f"remain: {error}")

    try:
        balance["lifetime_usage_usd"] = round(client.usage() / 100.0, 6)
    except ahb_api.ApiError as error:
        errors.append(f"usage: {error}")

    if balance["total"] is None and balance["remaining"] is not None:
        base_used = balance["lifetime_usage_usd"] or 0.0
        balance["total"] = round(base_used + balance["remaining"], 6)
    if balance["total"] is not None and balance["remaining"] is not None:
        balance["used"] = round(balance["total"] - balance["remaining"], 6)
    elif balance["lifetime_usage_usd"] is not None:
        balance["used"] = balance["lifetime_usage_usd"]

    if balance["total"] and balance["total"] > 0 and balance["used"] is not None:
        used_pct = balance["used"] / balance["total"]
        balance["used_pct"] = min(max(used_pct, 0.0), 1.0)
        balance["remaining_pct"] = min(max(1.0 - used_pct, 0.0), 1.0)

    if manage_key:
        try:
            profile = client.user_self(manage_key)
            report["profile"] = {
                "username": profile.get("username", ""),
                "display_name": profile.get("display_name", ""),
            }
        except ahb_api.ApiError as error:
            errors.append(f"user/self: {error}")
        try:
            unit = report["quota_per_unit"] or 500000
            for token in client.tokens(manage_key):
                report["keys"].append(
                    {
                        "id": token.get("id"),
                        "name": (token.get("name") or "").strip() or "(unnamed)",
                        "unlimited": bool(token.get("unlimited_quota")),
                        "used_usd": round(float(token.get("used_quota") or 0) / unit, 6),
                        "remain_usd": round(float(token.get("remain_quota") or 0) / unit, 6),
                        "status": token.get("status"),
                        "accessed": _format_date(token.get("accessed_time")),
                    }
                )
        except ahb_api.ApiError as error:
            errors.append(f"tokens: {error}")

    if include_models:
        try:
            report["models"] = client.models()
            report["model_count"] = len(report["models"])
        except ahb_api.ApiError as error:
            errors.append(f"models: {error}")

    entries = ahb_history.load(history_path or ahb_history.history_path())
    if entries:
        report["history"] = {"present": True, **ahb_history.summarize(entries, days)}

    report["days_left"] = _days_left(balance, report["history"])
    return report


def record_snapshot(report: dict, path, days: int | None = None) -> dict | None:
    """Append the current balance to the history file and refresh the in-report summary."""
    balance = report["balance"]
    if balance["used"] is None or balance["remaining"] is None:
        return None
    entry = ahb_history.record(
        path,
        {
            "used": balance["used"],
            "remaining": balance["remaining"],
            "total": balance["total"] or 0.0,
        },
    )
    entries = ahb_history.load(path)
    report["history"] = {"present": True, **ahb_history.summarize(entries, days)}
    report["days_left"] = _days_left(balance, report["history"])
    return entry


def _opt_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_date(milliseconds) -> str:
    if not milliseconds:
        return ""
    try:
        return datetime.datetime.fromtimestamp(float(milliseconds)).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return ""


def _days_left(balance: dict, history: dict) -> float | None:
    burn = history.get("burn_per_day") or 0.0
    remaining = balance.get("remaining")
    if burn > 0 and remaining is not None:
        return remaining / burn
    return None


def _balance_style(report: dict, console: render.Console):
    name = _severity(
        report["balance"]["remaining_pct"], report["warn_pct"], report["crit_pct"]
    )
    return getattr(console, name)


def render_report(report: dict, console: render.Console) -> None:
    """Write the full usage report to the console."""
    balance = report["balance"]
    account = report["system"].get("system_name") or "AIHubMix"

    header = [
        f"endpoint     {report['base']}",
        f"account      {account}   key {report['key_hint']}",
        f"generated    {report['generated_at']}",
    ]
    render.panel("AIHubMix Usage", header, console)

    _render_balance(report, console)
    _render_account(report, console)
    if report["keys"]:
        _render_keys(report, console)
    if report["history"].get("present"):
        _render_history(report, console)
    if report["errors"]:
        console.section("Problems")
        for message in report["errors"]:
            console.warning(f"  ! {message}")


def _render_balance(report: dict, console: render.Console) -> None:
    balance = report["balance"]
    console.section("Balance")
    if balance["total"] is None:
        console.write("  " + console.dim("total unavailable"))
    else:
        console.line("total", render.human_usd(balance["total"]))
    console.line(
        "used",
        render.human_usd(balance["used"]) if balance["used"] is not None else "-",
    )

    style = _balance_style(report, console)
    remaining = render.human_usd(balance["remaining"]) if balance["remaining"] is not None else "-"
    if balance["remaining_pct"] is not None:
        remaining += console.dim(f"   {render.ratio_text(balance['remaining_pct'])} of quota")
    console.line("remaining", style(console.bold(remaining)))

    if balance["used_pct"] is not None:
        bar = style(render.bar(balance["used_pct"]))
        console.write(
            f"  {render.pad('', 14)}{bar}  {render.ratio_text(balance['used_pct'])} used"
        )
        if balance["remaining_pct"] <= report["warn_pct"]:
            console.warning(
                f"  low balance: {render.ratio_text(balance['remaining_pct'])} of quota remaining"
            )

    if balance["lifetime_usage_usd"] is not None:
        note = console.dim("  reported by /dashboard/billing/usage")
        console.line("lifetime", render.human_usd(balance["lifetime_usage_usd"]) + note)

    history = report["history"]
    if history.get("burn_per_day"):
        console.line("burn", f"{render.human_usd(history['burn_per_day'])} / day")
        if report["days_left"] is not None:
            console.line(
                "projected",
                console.bold(f"≈ {report['days_left']:.1f} days of balance left at this rate"),
            )


def _render_account(report: dict, console: render.Console) -> None:
    console.section("Account")
    unit = report["quota_per_unit"]
    console.line("endpoint", report["base"])
    if report["system"].get("system_name"):
        console.line("system", report["system"]["system_name"])
    console.line("unit", f"{render.human_count(unit)} quota = $1")
    balance = report["balance"]
    if balance["has_payment_method"] is not None:
        console.line("payment", "card on file" if balance["has_payment_method"] else "none")
    if report.get("profile"):
        name = report["profile"].get("display_name") or report["profile"].get("username")
        if name:
            console.line("user", name)
    console.line("key", report["key_hint"])


def _render_keys(report: dict, console: render.Console) -> None:
    console.section(f"Keys ({len(report['keys'])})")
    table = render.Table(
        [
            ("id", "right", "dim"),
            ("used", "right", "yellow"),
            ("remain", "right", "green"),
            ("state", "left", "dim"),
            ("accessed", "left", "dim"),
            ("name", "left", ""),
        ]
    )
    for key in sorted(report["keys"], key=lambda item: item["used_usd"], reverse=True):
        state = "unlimited" if key["unlimited"] else ("active" if key["status"] == 1 else "off")
        remain = "∞" if key["unlimited"] else render.human_usd(key["remain_usd"])
        table.add(
            key["id"],
            render.human_usd(key["used_usd"]),
            remain,
            state,
            key["accessed"] or "-",
            render.truncate(key["name"], 30),
        )
    table.write(console)


def _render_history(report: dict, console: render.Console) -> None:
    history = report["history"]
    console.section("Spend history")
    if not history.get("present"):
        console.write("  " + console.dim("no snapshots yet — run `history` to start tracking"))
        return
    console.line(
        "snapshots",
        f"{render.human_count(history['snapshots'])}   {history['first']} → {history['last']}",
    )
    console.line("tracked", f"{history['span_days']:g} days")
    if history.get("burn_per_day"):
        console.line("burn", f"{render.human_usd(history['burn_per_day'])} / day")
    daily = history.get("daily") or []
    if not daily:
        console.write("  " + console.dim("not enough snapshots yet — run history again later"))
        return
    console.write("")
    largest = max(row["spend"] for row in daily) or 1.0
    table = render.Table(
        [
            ("date", "left", "dim"),
            ("spend", "right", "yellow"),
            ("", "left", "cyan"),
        ]
    )
    for row in daily:
        table.add(row["date"], render.human_usd(row["spend"]), render.bar(row["spend"] / largest))
    table.write(console)


def render_models(models: list[dict], query: str, console: render.Console, limit: int = MODEL_LIMIT) -> None:
    needle = query.lower()
    matched = [m for m in models if not needle or needle in str(m.get("id", "")).lower()]
    console.section(f"Models ({render.human_count(len(matched))})")
    table = render.Table([("owner", "left", "dim"), ("model", "left", "")])
    for model in matched[:limit]:
        table.add(model.get("owned_by", ""), model.get("id", ""))
    table.write(console)
    if len(matched) > limit:
        console.write("  " + console.dim(f"… {render.human_count(len(matched) - limit)} more"))
