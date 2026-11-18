"""Self-contained HTML report."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from sru_scout.models import LEVELS, BugReview, IssueReview, SeriesStatus

_env = Environment(loader=PackageLoader("sru_scout", "templates"), autoescape=select_autoescape(["html", "j2"]))

LABELS = {
    "blocked": "Blocked",
    "engineer-attention": "Engineer",
    "sponsor-attention": "Sponsor",
    "review": "To review",
    "ready": "Ready",
    "done": "Done",
}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_RELEASE_STATE = {"Fix Released": "released", "Fix Committed": "committed", "In Progress": "progress", "Incomplete": "incomplete"}


def _newest_first(s: SeriesStatus) -> tuple[bool, bool, tuple[int, ...]]:
    version = tuple(-int(part) for part in s.version.split(".") if part.isdigit())
    return (s.series != "devel", not version, version)


def release_groups(bug: BugReview) -> list[dict[str, Any]]:
    """Colour-coded release chips grouped by source component."""
    by_component_release: dict[tuple[str, str], SeriesStatus] = {}
    unscoped = []
    for s in bug.series:
        if not s.package:
            unscoped.append(s)
            continue
        key = (s.package, s.codename or s.series)
        current = by_component_release.get(key)
        if current is None or (current.series == "devel" and s.series != "devel"):
            by_component_release[key] = s

    groups: dict[str, dict[str, Any]] = {}
    for s in sorted([*by_component_release.values(), *unscoped], key=_newest_first):
        package = s.package or bug.package or "?"
        name = s.codename or s.series
        label = f"{name} (devel)" if s.series == "devel" and s.codename else name
        title = f"{label} {s.version}".rstrip() + f": {s.status}" + (" (EOL)" if s.eol else "")
        state = _RELEASE_STATE.get(s.status, "closed" if s.closed else "open")
        group = groups.setdefault(package, {"package": package, "chips": []})
        group["chips"].append({"letter": name[0].upper(), "cls": state + (" eol" if s.eol else ""), "title": title})

    package_order = [package.strip() for package in bug.package.split(",") if package.strip()]
    ordered = [package for package in package_order if package in groups]
    ordered.extend(package for package in groups if package not in ordered)
    return [groups[package] for package in ordered]


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
        row["queued"] = [q for q in bug.queued if q.series == row["series"]]
        row["excuses"] = [e for e in bug.excuses if e.series == row["series"]]
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


def main_issue(bug: BugReview, limit: int = 160) -> dict[str, Any] | None:
    """Top finding of an open bug for the overview table."""
    if bug.level == "done" or not bug.findings:
        return None
    text = " ".join(bug.findings[0].text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return {"text": text, "more": len(bug.findings) - 1}


def unique_bugs(reviews: list[IssueReview]) -> list[BugReview]:
    """Every Launchpad bug once, even when several reviews reference it; the bug number is the report's ID."""
    bugs: dict[int, BugReview] = {}
    for review in reviews:
        for bug in review.bugs:
            bugs.setdefault(bug.bug_id, bug)
    return sorted(bugs.values(), key=lambda b: (LEVELS.index(b.level), b.bug_id))


_env.globals.update(
    series_rows=series_rows, artifacts=artifacts, labels=LABELS, main_issue=main_issue, release_groups=release_groups
)


def render_html(reviews: list[IssueReview], title: str = "Ubuntu Stable Release Updates") -> str:
    bugs = unique_bugs(reviews)
    counts = Counter(b.level for b in bugs)
    return _env.get_template("report.html.j2").render(
        title=title,
        bugs=bugs,
        levels=LEVELS,
        counts={level: counts.get(level, 0) for level in LEVELS},
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
