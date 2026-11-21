"""CLI.

  python3 -m signal_engine run [--dry-run] [--as-of 2025-06-02T09:00]
  python3 -m signal_engine explain <domain>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brief import build_briefs, why, write_briefs
from .engine import load_accounts, load_config, load_events, normalize_domain, parse_ts, score_accounts

ROOT = Path(__file__).resolve().parent.parent


def _score(args):
    cfg = load_config(args.config)
    accounts = load_accounts(args.data / "accounts.csv")
    events = load_events(args.data)
    as_of = parse_ts(args.as_of) if args.as_of else None
    scores, unknown, as_of = score_accounts(accounts, events, cfg, as_of)
    return cfg, accounts, events, scores, unknown, as_of


def cmd_run(args) -> int:
    cfg, accounts, events, scores, unknown, as_of = _score(args)
    print(f"as of {as_of:%Y-%m-%d %H:%M} | {len(accounts)} accounts after domain dedupe | {len(events)} events")
    for s in scores:
        print(f"  {s.total:>6}  {s.account.domain:<26} {s.account.owner}")
    if unknown:
        print("skipped: " + ", ".join(f"{k} ({v})" for k, v in sorted(unknown.items())))
    briefs = build_briefs(scores, cfg, as_of)
    print("\n".join(write_briefs(briefs, args.out, dry_run=args.dry_run)))
    if args.dry_run:
        print("dry run: nothing written")
    return 0


def cmd_explain(args) -> int:
    _, _, _, scores, _, as_of = _score(args)
    target = normalize_domain(args.domain)
    match = next((s for s in scores if s.account.domain == target), None)
    if match is None:
        print(f"no account with domain {target}", file=sys.stderr)
        return 1
    a = match.account
    print(f"{a.name} ({a.domain})  score {match.total}  owner {a.owner}")
    print(f"merged records: {', '.join(a.source_ids)} | region {a.region} | {a.employees} employees")
    print(f"as of {as_of:%Y-%m-%d %H:%M}\n")
    print(f"  {'signal':<22} {'when':<17} {'age':>6} {'points':>7}  detail")
    for h in sorted(match.hits, key=lambda h: h.ts, reverse=True):
        print(f"  {h.signal:<22} {h.ts:%Y-%m-%d %H:%M} {h.age_days:>5}d {h.points:>+7.2f}  {h.detail}")
    for sig, n in match.capped.items():
        print(f"  {sig:<22} {n} more ignored (max_count reached)")
    print("\nwhy:")
    for line in why(match):
        print(f"  - {line}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="signal_engine", description="Nightly account scoring and rep briefs.")
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "rules.json")
    p.add_argument("--as-of", help="score as of this time (default: newest event in the feeds)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="score every account and write rep briefs")
    r.add_argument("--out", type=Path, default=ROOT / "out" / "briefs")
    r.add_argument("--dry-run", action="store_true", help="score and report, write nothing")
    e = sub.add_parser("explain", help="show every signal behind one account's score")
    e.add_argument("domain")
    args = p.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_explain(args)


if __name__ == "__main__":
    sys.exit(main())
