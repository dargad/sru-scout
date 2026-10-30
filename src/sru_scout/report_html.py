"""Self-contained HTML report."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from sru_scout.models import LEVELS, BugReview, IssueReview

_env = Environment(loader=PackageLoader("sru_scout", "templates"), autoescape=select_autoescape(["html", "j2"]))

LABELS = {
    "blocked": "Blocked",
    "engineer-attention": "Engineer attention",
    "sponsor-attention": "Sponsor attention",
    "review": "To review",
    "ready": "Ready",
    "done": "Done",
}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def series_rows(bug: BugReview) -> list[dict[str, Any]]:
    """One row per series, joining the Launchpad task, the -proposed entry and attributed patches/MPs."""
    proposed = {p.series: p for p in bug.pending}
    rows = []
    seen = set()
    for s in bug.series:
        seen.add(s.series)
        state = "fixed" if s.fixed else "eol" if s.eol else "closed" if s.closed else "pending"
        rows.append({"series": s.series, "status": s.status, "state": state, "url": s.url, "proposed": proposed.get(s.series)})
    for name, entry in proposed.items():
        if name not in seen:
            rows.append({"series": name, "status": "no task", "state": "closed", "url": "", "proposed": entry})
    for row in rows:
        row["artifacts"] = ["patch" for p in bug.patches if row["series"] in p.series]
        row["artifacts"] += [f"MP ({mp.status})" for mp in bug.merge_proposals if mp.series == row["series"]]
    return rows


def artifacts(bug: BugReview) -> list[dict[str, Any]]:
    """Patches and merge proposals in one list, with lint findings sorted by severity."""
    result = []
    for p in bug.patches:
        result.append({"name": p.title, "url": p.web_url, "series": p.series, "note": "", "lint": p.lint})
    for mp in bug.merge_proposals:
        note = " · ".join([mp.status, *mp.votes])
        result.append({"name": f"MP {mp.source} → {mp.target}", "url": mp.web_url, "series": [mp.series] if mp.series else [], "note": note, "lint": mp.lint})
    for item in result:
        lint = item["lint"]
        item["findings"] = sorted(lint.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3)) if lint else []
    return result


def main_issues(review: IssueReview, limit: int = 160) -> list[dict[str, Any]]:
    """Top finding of each open bug for the overview table; the LP label is only shown for multi-bug cards."""
    rows = []
    for bug in review.bugs:
        if bug.level == "done" or not bug.findings:
            continue
        text = " ".join(bug.findings[0].text.split())
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        rows.append({"label": bug.label if len(review.bugs) > 1 else "", "text": text, "more": len(bug.findings) - 1})
    if not review.bugs:
        rows.append({"label": "", "text": review.reasons[0] if review.reasons else "", "more": 0})
    return rows


_env.globals.update(series_rows=series_rows, artifacts=artifacts, labels=LABELS, main_issues=main_issues)


def render_html(reviews: list[IssueReview], title: str = "Ubuntu Stable Release Updates") -> str:
    ordered = sorted(reviews, key=lambda r: (LEVELS.index(r.level), r.key))
    counts = Counter(r.level for r in reviews)
    return _env.get_template("report.html.j2").render(
        title=title,
        reviews=ordered,
        todo=[r for r in ordered if r.level != "done"],
        finished=[r for r in ordered if r.level == "done"],
        levels=LEVELS,
        counts={level: counts.get(level, 0) for level in LEVELS},
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
