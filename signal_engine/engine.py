"""Load feeds, dedupe accounts by domain, score with time-decayed rules, assign owners."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Account:
    domain: str
    name: str
    region: str
    employees: int
    source_ids: List[str] = field(default_factory=list)
    owner: str = ""


@dataclass
class Event:
    domain: str
    signal: str
    ts: datetime
    detail: str = ""


@dataclass
class Hit:
    """One event's contribution to an account score."""
    signal: str
    label: str
    ts: datetime
    age_days: float
    points: float
    detail: str


@dataclass
class Score:
    account: Account
    total: float
    hits: List[Hit]
    capped: Counter


def normalize_domain(value: str) -> str:
    """'https://www.Acme.example/about' and 'acme.example' are the same account."""
    v = (value or "").strip().lower()
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/", 1)[0].split(":", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", ""))


# ------------------------------------------------------------------ loading
def load_accounts(path: Path) -> Dict[str, Account]:
    """Read accounts and merge rows that share a domain. The first row keeps its name."""
    accounts: Dict[str, Account] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            domain = normalize_domain(row["website"])
            if not domain:
                continue
            if domain in accounts:
                accounts[domain].source_ids.append(row["account_id"])
                continue
            accounts[domain] = Account(domain, row["name"].strip(), row["region"].strip(),
                                       int(row["employees"] or 0), [row["account_id"]])
    return accounts


def _csv(path: Path):
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load_events(data_dir: Path) -> List[Event]:
    """Turn each feed into one shape: (domain, signal, timestamp)."""
    ev: List[Event] = []
    for r in _csv(data_dir / "crm_stage_changes.csv"):
        ev.append(Event(normalize_domain(r["domain"]), f"stage:{r['to_stage']}", parse_ts(r["changed_at"]),
                        f"{r['from_stage']} -> {r['to_stage']}"))
    for r in _csv(data_dir / "email_events.csv"):
        ev.append(Event(normalize_domain(r["email"]), f"email_{r['event']}", parse_ts(r["ts"]), r["email"]))
    usage = data_dir / "product_usage.json"
    for r in (json.loads(usage.read_text()) if usage.exists() else []):
        ev.append(Event(normalize_domain(r["domain"]), f"product:{r['event']}", parse_ts(r["ts"])))
    for r in _csv(data_dir / "web_visits.csv"):
        kind = "web:pricing" if r["path"].startswith("/pricing") else "web:visit"
        ev.append(Event(normalize_domain(r["domain"]), kind, parse_ts(r["ts"]), r["path"]))
    for r in _csv(data_dir / "proposal_views.csv"):
        ev.append(Event(normalize_domain(r["domain"]), "proposal_view", parse_ts(r["ts"]),
                        f"{r['viewer_email']}, {r['seconds']}s"))
    return ev


# ------------------------------------------------------------------ scoring
def decay(weight: float, age_days: float, half_life_days: float) -> float:
    """Exponential decay: a signal is worth half as much after one half-life."""
    if age_days < 0:
        return 0.0  # events after the as-of date do not count yet
    return weight * 0.5 ** (age_days / half_life_days)


def assign_owner(account: Account, territories: List[dict], default: str) -> str:
    """First matching rule wins, so order the config from most to least specific."""
    for t in territories:
        if "min_employees" in t and account.employees < t["min_employees"]:
            continue
        if "regions" in t and account.region not in t["regions"]:
            continue
        return t["owner"]
    return default


def score_accounts(accounts: Dict[str, Account], events: List[Event], config: dict,
                   as_of: Optional[datetime] = None):
    """Return (scores sorted high to low, counter of signals the config does not know)."""
    rules = config["signals"]
    as_of = as_of or max((e.ts for e in events), default=datetime.now())
    by_domain: Dict[str, List[Event]] = defaultdict(list)
    unknown: Counter = Counter()
    for e in events:
        if e.signal not in rules:
            unknown[e.signal] += 1
        elif e.domain in accounts:
            by_domain[e.domain].append(e)
        else:
            unknown[f"no-account:{e.domain}"] += 1

    scores = []
    for domain, acct in accounts.items():
        acct.owner = assign_owner(acct, config["territories"], config["default_owner"])
        hits: List[Hit] = []
        seen: Counter = Counter()
        capped: Counter = Counter()
        # newest first, so the cap keeps the freshest events
        for e in sorted(by_domain.get(domain, []), key=lambda x: x.ts, reverse=True):
            rule = rules[e.signal]
            if seen[e.signal] >= rule.get("max_count", 1_000_000):
                capped[e.signal] += 1
                continue
            seen[e.signal] += 1
            age = (as_of - e.ts).total_seconds() / 86400
            pts = decay(rule["weight"], age, rule["half_life_days"])
            hits.append(Hit(e.signal, rule["label"], e.ts, round(age, 1), round(pts, 2), e.detail))
        scores.append(Score(acct, round(sum(h.points for h in hits), 1), hits, capped))
    scores.sort(key=lambda s: (-s.total, s.account.domain))
    return scores, unknown, as_of


def load_config(path: Path) -> dict:
    cfg = json.loads(Path(path).read_text())
    for name, rule in cfg["signals"].items():
        if rule["half_life_days"] <= 0:
            raise ValueError(f"{name}: half_life_days must be positive")
    return cfg
