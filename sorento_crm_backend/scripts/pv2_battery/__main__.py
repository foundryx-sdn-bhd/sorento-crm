"""PV2 scenario battery CLI (PV2-EVAL).

    python -m scripts.pv2_battery run --name NAME [--mode v2|v1|both] [--ids a,b] [--domain d]
        [--repeat N] [--parallel N] [--no-judge] [--out DIR] [--judge-key-file F]
        [--v2-version ID] [--v1-version ID]
    python -m scripts.pv2_battery calibrate [--judge-key-file F]
    python -m scripts.pv2_battery check

Safety story: nothing leaves this process. Every turn is `console_service.run_console_turn`
(console_service.py:444) with the envelope patched by `guard.patch_envelope`: `console_origin` is
dropped so `engine._chat_console` is false and the stock-ask notify returns early (engine.py:9803,
stock_ask_service.py:434 notify_salesman). `pv2_flag.parser_mode` (pv2/flag.py:41, called at
engine.py:4681) is forced per mode in this process. `guard.install` runs BEFORE the engine is used and
refuses every RQ enqueue (`rq.Queue.enqueue_call`, `queue_service.enqueue_job` queue_service.py:116 and
its binding stock_ask_service.py:25, called :238) and every Respond send (`send_text_or_template`
respond_messaging_service.py:570, bound at stock_ask_service.py:26, called :482). It also refuses to
start unless HTTPS_PROXY is the dead proxy and DATABASE_URL is a local non-prod database. A refused call
fails the turn with check `outbound_blocked` and the run goes on. Envelopes stay `is_test` with a fresh
run_id per (conversation, mode, rep).
"""
from __future__ import annotations

import argparse
import sys

from . import schema


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m scripts.pv2_battery")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the battery through the console turn")
    r.add_argument("--name", required=True)
    r.add_argument("--mode", choices=("v2", "v1", "both"), default="v2")
    r.add_argument("--ids")
    r.add_argument("--domain", choices=schema.DOMAINS)
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--parallel", type=int, default=4)
    r.add_argument("--no-judge", action="store_true")
    r.add_argument("--out")
    r.add_argument("--judge-key-file")
    r.add_argument("--v2-version")
    r.add_argument("--v1-version")
    r.add_argument("--no-embedding-key", action="store_true")
    r.add_argument("--battery-dir", help=argparse.SUPPRESS)
    c = sub.add_parser("calibrate", help="run the judge over calibration.yaml")
    c.add_argument("--judge-key-file")
    sub.add_parser("check", help="validate the battery only, no model calls")
    return p


def _check() -> int:
    problems: list[str] = []
    try:
        convs = schema.load_battery()
        problems += schema.validate_coverage(convs)
        schema.load_contacts()
        cal = schema.load_calibration()
    except schema.BatteryError as exc:
        print(f"battery invalid: {exc}")
        return 1
    for p in problems:
        print(f"coverage: {p}")
    print(f"{len(convs)} conversations, {len(cal)} calibration turns, {len(problems)} coverage problems")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "check":
        return _check()
    from . import runner

    if args.cmd == "calibrate":
        runner.calibrate(args)
        return 0
    runner.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
