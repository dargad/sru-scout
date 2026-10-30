"""Console report built with rich."""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.text import Text

from sru_scout.models import LEVELS, BugReview, IssueReview

STYLES = {
    "blocked": "bold red",
    "engineer-attention": "yellow",
    "sponsor-attention": "magenta",
    "review": "cyan",
    "ready": "green",
    "done": "dim green",
}
LABELS = {
    "blocked": "BLOCKED",
    "engineer-attention": "ENGINEER-ATTENTION",
    "sponsor-attention": "SPONSOR-ATTENTION",
    "review": "REVIEW",
    "ready": "READY",
    "done": "DONE",
}


def level_text(level: str) -> Text:
    return Text(LABELS[level], style=STYLES[level])


def _why(reasons: list[str], limit: int = 140) -> str:
    """Most important reason (reasons are ordered worst-first), plus how many others there are."""
    first = " ".join(reasons[0].split())
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    return f"{first} (+{len(reasons) - 1} more)" if len(reasons) > 1 else first


def progress_line(done: int, total: int, review: IssueReview) -> Text:
    """Counter, Jira key and level, then per bug `LP: #n (package) title` and, unless DONE, a short reason."""
    text = Text(f"[{done}/{total}] {review.key} ")
    text.append_text(level_text(review.level))
    for bug in review.bugs:
        title = bug.short_title(80) if bug.title else f"({bug.error or 'title unavailable'})"
        text.append(f"\n    {bug.label} ({bug.package or '?'}) {title}")
        if bug.level != "done" and bug.attention_reasons:
            text.append(f"\n        -> {_why(bug.attention_reasons)}", style=STYLES[bug.level])
    if not review.bugs:
        text.append(f"\n    (no Launchpad bug found) {review.summary[:80]}")
        if review.level != "done" and review.reasons:
            text.append(f"\n        -> {_why(review.reasons)}", style=STYLES[review.level])
    return text


def series_summary(bug: BugReview) -> Text:
    text = Text()
    for s in bug.series:
        style = "green" if s.fixed else "dim" if (s.closed or s.eol) else "yellow"
        text.append(f"{s.series}:{s.status}", style=style)
        text.append("  ")
    for p in bug.pending:
        flags = f" [{', '.join(p.flags)}]" if p.flags else ""
        days = f" {p.days}d" if p.days is not None else ""
        text.append(f"proposed:{p.series}{days}{flags}", style="magenta")
        text.append("  ")
    return text


def _why_lines(review: IssueReview, limit: int = 3) -> Text:
    """Top reasons per bug (already worst first); the LP label is only repeated when a card has several bugs."""
    text = Text()
    for bug in review.bugs:
        if bug.level == "done":
            continue
        if len(review.bugs) > 1:
            text.append(f"{bug.label}\n", style="bold")
        for f in bug.findings[:limit]:
            text.append(f"• {f.text}\n", style=STYLES.get(f.level, ""))
        if len(bug.findings) > limit:
            text.append(f"  (+{len(bug.findings) - limit} more)\n", style="dim")
    if not review.bugs:
        text.append("\n".join(f"• {r}" for r in review.reasons))
    text.rstrip()
    return text


def render(reviews: list[IssueReview], console: Console, verbose: bool = False) -> None:
    ordered = sorted(reviews, key=lambda r: (LEVELS.index(r.level), r.key))
    counts = Counter(r.level for r in reviews)

    summary = Text("Summary: ")
    for level in LEVELS:
        summary.append(f"{counts.get(level, 0)} {LABELS[level].lower()}  ", style=STYLES[level])
    console.print(summary)

    table = Table(show_lines=True, expand=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Jira")
    table.add_column("Launchpad bug / series")
    table.add_column("Why")
    for review in ordered:
        if review.level == "done" and not verbose:
            continue
        bugs = Text()
        for bug in review.bugs:
            bugs.append(f"{bug.label}  {bug.package or '?'}\n", style="bold")
            bugs.append(f"{bug.short_title()}\n" if bug.title else "(title unavailable)\n")
            bugs.append_text(series_summary(bug))
            bugs.append("\n")
        table.add_row(
            level_text(review.level),
            Text.assemble((review.key, "bold"), f"\n{review.summary[:70]}\n{review.assignee}"),
            bugs or Text("-"),
            _why_lines(review),
        )
    console.print(table)
    hidden = counts.get("done", 0)
    if hidden and not verbose:
        console.print(f"[dim]{hidden} fully fixed card(s) hidden; use --verbose to list them.[/dim]")

    if verbose:
        for review in ordered:
            for bug in review.bugs:
                _detail(bug, console)


def _detail(bug: BugReview, console: Console) -> None:
    console.rule(f"{bug.label} [{bug.package or '?'}] {bug.title}")
    console.print(bug.url)
    for artifact in [*bug.patches, *bug.merge_proposals]:
        name = getattr(artifact, "title", None) or artifact.web_url
        lint = artifact.lint
        console.print(f"[bold]{name}[/bold]")
        if lint is None:
            continue
        if not lint.ran:
            console.print(f"  [red]sru-lint failed: {lint.error}[/red]")
        for f in lint.findings:
            console.print(f"  {f.severity.upper():7} {f.rule_id}: {f.message}  ({f.path}:{f.line})")
    for b in bug.blockers:
        suffix = " (possibly resolved)" if b.maybe_resolved else ""
        console.print(f"[red]{b.severity}[/red] {b.summary}{suffix} {b.url}")
