#!/usr/bin/env python3
"""Command-line entry point for inspecting AIHubMix balance, usage, and spend history."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import ahb_api
import ahb_history
import ahb_render
import ahb_report


BASE_ENV = ("AIHUB", "AIHUBMIX_BASE", "AIHUB_DIRECT")
KEY_ENV = ("AIHUB_KEY", "AIHUBMIX_API_KEY", "AIHUB_API_KEY")
MANAGE_ENV = ("AIHUB_MANAGE_KEY", "AIHUBMIX_MANAGE_KEY", "AIHUB_ACCESS_TOKEN")


def _first_env(names) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def resolve_base(explicit: str | None) -> str:
    return (explicit or _first_env(BASE_ENV) or ahb_api.DEFAULT_BASE).strip()


def resolve_key(explicit: str | None) -> str:
    return explicit or _first_env(KEY_ENV)


def resolve_manage(explicit: str | None) -> str:
    return explicit or _first_env(MANAGE_ENV)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", default=None, help="API base URL (default: $AIHUB, then $AIHUB_DIRECT)")
    common.add_argument("--key", default=None, help="API key (default: $AIHUB_KEY)")
    common.add_argument(
        "--manage-key",
        default=None,
        help="Manage Key / access token for per-key data (default: $AIHUB_MANAGE_KEY)",
    )
    common.add_argument("--timeout", type=float, default=20.0, help="request timeout seconds (default: 20)")
    common.add_argument("--retries", type=int, default=5, help="retries per request (default: 5)")
    common.add_argument("--data-dir", type=Path, default=None, help="snapshot directory (default: XDG data home)")
    common.add_argument("--no-color", action="store_true", help="disable ANSI color")
    common.add_argument("--json", action="store_true", help="emit JSON instead of a rendered report")

    parser = argparse.ArgumentParser(
        prog="aihubmix_usage",
        description="Visualize AIHubMix balance, usage, models, and spend history.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", parents=[common], help="full balance and usage overview")
    report.add_argument("--models", action="store_true", help="include the available model list")
    report.add_argument("--filter", default="", help="filter models by substring")
    report.add_argument("--limit", type=int, default=50, help="models to show (default: 50)")
    report.add_argument("--warn-pct", type=float, default=20.0, help="low-balance warning threshold (default: 20)")
    report.add_argument("--crit-pct", type=float, default=10.0, help="critical-balance threshold (default: 10)")

    history = subparsers.add_parser("history", parents=[common], help="record and view spend over time")
    history.add_argument("--days", type=int, default=14, help="days of history to show (default: 14)")
    history.add_argument("--no-record", action="store_true", help="view the trend without adding a snapshot")

    models = subparsers.add_parser("models", parents=[common], help="list available models")
    models.add_argument("--filter", default="", help="filter by substring")
    models.add_argument("--limit", type=int, default=50, help="models to show (default: 50)")

    watch = subparsers.add_parser("watch", parents=[common], help="poll the balance and show the running delta")
    watch.add_argument("--interval", type=float, default=15.0, help="seconds between polls (default: 15)")
    watch.add_argument("--count", type=int, default=0, help="polls to run, 0 means until interrupted")
    watch.add_argument("--days", type=int, default=14, help="days of history for burn estimate (default: 14)")
    return parser


def make_client(args) -> ahb_api.Client:
    key = resolve_key(getattr(args, "key", None))
    if not key:
        raise ahb_api.ApiError("no API key found; set AIHUB_KEY or pass --key")
    return ahb_api.Client(
        base_url=resolve_base(getattr(args, "base", None)),
        key=key,
        timeout=getattr(args, "timeout", 20.0),
        retries=getattr(args, "retries", 5),
    )


def _emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def run_report(args) -> int:
    client = make_client(args)
    report = ahb_report.build_report(
        client,
        manage_key=resolve_manage(args.manage_key),
        include_models=args.models,
        days=None,
        warn_pct=args.warn_pct,
        crit_pct=args.crit_pct,
        history_path=ahb_history.history_path(args.data_dir),
    )
    console = _console(args)
    if args.json:
        _emit_json(report)
        return 0
    ahb_report.render_report(report, console)
    if args.models and report["models"]:
        ahb_report.render_models(report["models"], args.filter, console, args.limit)
    return 0


def run_history(args) -> int:
    client = make_client(args)
    path = ahb_history.history_path(args.data_dir)
    report = ahb_report.build_report(
        client,
        manage_key=resolve_manage(args.manage_key),
        days=args.days,
        history_path=path,
    )
    if args.json:
        if not args.no_record:
            ahb_report.record_snapshot(report, path, args.days)
        _emit_json({"recorded": not args.no_record, "history": report["history"]})
        return 0

    console = _console(args)
    before = report["history"].get("snapshots", 0)
    if not args.no_record:
        ahb_report.record_snapshot(report, path, args.days)
    after = report["history"].get("snapshots", 0)

    ahb_render.panel(
        "AIHubMix Spend History",
        [
            f"endpoint     {report['base']}",
            f"snapshots    {after}   stored in {path}",
            f"generated    {report['generated_at']}",
        ],
        console,
    )
    if not args.no_record:
        console.write(
            "  "
            + console.dim(
                f"recorded snapshot {after}" if after > before else "snapshot unchanged"
            )
        )
    ahb_report._render_history(report, console)
    if report["errors"]:
        console.section("Problems")
        for message in report["errors"]:
            console.warning(f"  ! {message}")
    return 0


def run_models(args) -> int:
    client = make_client(args)
    models = client.models()
    console = _console(args)
    if args.json:
        matched = [
            m for m in models if not args.filter or args.filter.lower() in str(m.get("id", "")).lower()
        ]
        _emit_json({"count": len(matched), "models": matched})
        return 0
    ahb_report.render_models(models, args.filter, console, args.limit)
    return 0


def run_watch(args) -> int:
    client = make_client(args)
    console = _console(args)
    unit = ahb_history.data_dir(args.data_dir)
    start_used = None
    previous_used = None
    polls = 0
    try:
        while args.count <= 0 or polls < args.count:
            report = ahb_report.build_report(
                client,
                manage_key=resolve_manage(args.manage_key),
                days=args.days,
                history_path=ahb_history.history_path(args.data_dir),
            )
            used = report["balance"]["used"]
            if used is not None:
                if start_used is None:
                    start_used = used
                session = used - start_used
                step = 0.0 if previous_used is None else used - previous_used
                previous_used = used
            else:
                session = step = 0.0
            if args.json:
                report["session_spend"] = session
                report["poll_delta"] = step
                print(json.dumps(report, ensure_ascii=False, default=str), flush=True)
            else:
                console.clear()
                ahb_report.render_report(report, console)
                console.section("Session")
                console.line("spend", ahb_render.human_usd(session))
                console.line("last poll", ahb_render.human_usd(step))
                console.write("  " + console.dim(f"history: {unit}   ctrl-c to stop"))
            polls += 1
            if args.count <= 0 or polls < args.count:
                time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        if not args.json:
            console.write("")
            console.write(console.dim("  stopped"))
    return 0


def _console(args) -> ahb_render.Console:
    if args.json or args.no_color:
        return ahb_render.Console(color=False)
    return ahb_render.Console()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "report": run_report,
        "history": run_history,
        "models": run_models,
        "watch": run_watch,
    }
    try:
        return handlers[args.command](args)
    except ahb_api.ApiError as error:
        ahb_render.Console(stream=sys.stderr).error(f"error: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
