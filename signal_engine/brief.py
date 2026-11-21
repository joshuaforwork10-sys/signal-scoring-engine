"""Render one markdown brief per owner and write it only when it changed."""
from __future__ import annotations

import os
import re
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, List


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def why(score) -> List[str]:
    """Group an account's hits by signal: 'Moved to proposal x1: +19.4'."""
    groups: "OrderedDict[str, list]" = OrderedDict()
    for h in sorted(score.hits, key=lambda h: -abs(h.points)):
        groups.setdefault(h.label, []).append(h)
    lines = []
    for label, hits in groups.items():
        pts = sum(h.points for h in hits)
        newest = min(h.age_days for h in hits)
        lines.append(f"{label} x{len(hits)}: {pts:+.1f} (latest {newest:g}d ago)")
    return lines


def render(owner: str, scores, as_of, top_n: int) -> str:
    out = [f"# Rep brief: {owner}", "", f"Signals as of {as_of:%Y-%m-%d %H:%M}. Top {top_n} accounts by score.", "",
           "| # | Account | Domain | Score |", "|---|---|---|---|"]
    for i, s in enumerate(scores[:top_n], 1):
        out.append(f"| {i} | {s.account.name} | {s.account.domain} | {s.total} |")
    out.append("")
    for i, s in enumerate(scores[:top_n], 1):
        out.append(f"## {i}. {s.account.name} ({s.total})")
        out += [f"- {line}" for line in why(s)]
        out.append("")
    return "\n".join(out)


def build_briefs(scores, config, as_of) -> Dict[str, str]:
    """owner -> markdown. Accounts under min_score stay out of the brief."""
    cfg = config["brief"]
    per_owner = defaultdict(list)
    for s in scores:
        if s.total >= cfg["min_score"]:
            per_owner[s.account.owner].append(s)
    return {o: render(o, rows, as_of, cfg["top_n"]) for o, rows in sorted(per_owner.items())}


def write_briefs(briefs: Dict[str, str], out_dir: Path, dry_run: bool = False) -> List[str]:
    """Return one status line per file. Unchanged files are not touched."""
    status = []
    for owner, text in briefs.items():
        path = Path(out_dir) / f"{slug(owner)}.md"
        shown = os.path.relpath(path)
        if path.exists() and path.read_text() == text:
            status.append(f"unchanged   {shown}")
            continue
        if dry_run:
            status.append(f"would write {shown}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text)
        os.replace(tmp, path)  # atomic: a reader never sees half a brief
        status.append(f"wrote       {shown}")
    return status
