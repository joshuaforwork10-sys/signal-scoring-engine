from datetime import datetime
from pathlib import Path

import pytest

from signal_engine.__main__ import main
from signal_engine.brief import build_briefs, write_briefs
from signal_engine.engine import (Account, Event, assign_owner, decay, load_accounts, load_config,
                                  load_events, normalize_domain, score_accounts)

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "config" / "rules.json")
NOW = datetime(2025, 6, 2, 9, 0)


def acct(domain="acme.example", region="US-East", employees=100):
    return {domain: Account(domain, "Acme", region, employees, ["A-1"])}


@pytest.mark.parametrize("raw", ["https://www.acme.example/about", "ACME.example", "jo@acme.example", "www.acme.example:443"])
def test_normalize_domain(raw):
    assert normalize_domain(raw) == "acme.example"


def test_decay_halves_after_one_half_life():
    assert decay(10, 0, 7) == 10
    assert decay(10, 7, 7) == pytest.approx(5)
    assert decay(10, 14, 7) == pytest.approx(2.5)


def test_future_events_count_zero():
    assert decay(10, -1, 7) == 0


def test_accounts_dedupe_by_domain():
    accounts = load_accounts(ROOT / "data" / "accounts.csv")
    nw = accounts["northwind-logistics.example"]
    assert nw.source_ids == ["A-100", "A-101"]
    assert nw.name == "Northwind Logistics"
    assert len(accounts) == 8


def test_max_count_caps_repeat_signals():
    events = [Event("acme.example", "email_open", datetime(2025, 6, d)) for d in range(1, 3)] * 4
    scores, _, _ = score_accounts(acct(), events, CFG, NOW)
    s = scores[0]
    assert len(s.hits) == CFG["signals"]["email_open"]["max_count"]
    assert s.capped["email_open"] == len(events) - len(s.hits)


def test_negative_signal_lowers_score():
    events = [Event("acme.example", "email_unsubscribe", NOW)]
    scores, _, _ = score_accounts(acct(), events, CFG, NOW)
    assert scores[0].total == -25


def test_unknown_signal_is_reported_not_scored():
    events = [Event("acme.example", "email_forwarded", NOW), Event("ghost.example", "email_open", NOW)]
    scores, unknown, _ = score_accounts(acct(), events, CFG, NOW)
    assert scores[0].total == 0
    assert unknown == {"email_forwarded": 1, "no-account:ghost.example": 1}


@pytest.mark.parametrize("region,employees,owner", [
    ("US-West", 5000, "Morgan Avery"),   # size rule comes first
    ("US-East", 200, "Jordan Kim"),
    ("US-West", 200, "Riley Chen"),
    ("APAC", 200, "Unassigned queue"),
])
def test_territory_assignment(region, employees, owner):
    a = Account("x.example", "X", region, employees)
    assert assign_owner(a, CFG["territories"], CFG["default_owner"]) == owner


def test_sample_ranking_is_stable():
    scores, _, _ = score_accounts(load_accounts(ROOT / "data" / "accounts.csv"), load_events(ROOT / "data"), CFG)
    assert [s.account.domain for s in scores[:3]] == ["contosohealth.example", "northwind-logistics.example", "fabrikam.example"]


def test_dry_run_writes_nothing_and_rerun_is_idempotent(tmp_path):
    scores, _, as_of = score_accounts(load_accounts(ROOT / "data" / "accounts.csv"), load_events(ROOT / "data"), CFG)
    briefs = build_briefs(scores, CFG, as_of)
    status = write_briefs(briefs, tmp_path, dry_run=True)
    assert all(s.startswith("would write") for s in status)
    assert list(tmp_path.iterdir()) == []
    write_briefs(briefs, tmp_path)
    again = write_briefs(briefs, tmp_path)
    assert all(s.startswith("unchanged") for s in again)
    text = (tmp_path / "jordan-kim.md").read_text()
    assert "Northwind Logistics" in text and "Opened the proposal page" in text


def test_explain_cli(capsys):
    assert main(["explain", "https://www.northwind-logistics.example"]) == 0
    out = capsys.readouterr().out
    assert "A-100, A-101" in out and "proposal_view" in out
    assert main(["explain", "nobody.example"]) == 1
