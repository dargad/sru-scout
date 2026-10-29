"""Turns collected facts into an attention level plus human-readable reasons."""

from __future__ import annotations

from sru_scout.models import LEVELS, BugReview, IssueReview

# pending-sru flag -> (level, explanation)
_PENDING_FLAGS = {
    "verification-failed": ("blocked", "verification failed"),
    "block-proposed": ("blocked", "tagged block-proposed, double-check before release"),
    "incomplete": ("attention", "bug is Incomplete"),
    "removal-candidate": ("attention", "candidate for removal from -proposed (no verification)"),
    "verified": ("review", "verified, can be released"),
    "messages": ("review", "new comments since it was accepted in -proposed"),
    "broken": ("review", "listed as duplicate/inaccessible"),
}


def _worst(*levels: str) -> str:
    return min(levels, key=LEVELS.index)


def classify_bug(bug: BugReview) -> None:
    """Fill bug.level and bug.attention_reasons in place."""
    found: list[tuple[str, str]] = []  # (level, reason)
    level = "ready"

    def flag(new_level: str, reason: str) -> None:
        nonlocal level
        level = _worst(level, new_level)
        found.append((new_level, reason))

    if not bug.accessible:
        bug.level = "attention"
        bug.attention_reasons = [bug.error or "Launchpad bug is not accessible (private?)"]
        return

    uploaded = {p.series for p in bug.pending}
    pending = [s for s in bug.series if s.pending]
    to_upload = [s for s in pending if s.series not in uploaded]
    if bug.series and not pending and not bug.pending:
        bug.level = "done"
        bug.attention_reasons = ["All series are Fix Committed/Released or closed"]
        return
    if not bug.series:
        flag("attention", "Bug has no Ubuntu package tasks")

    for p in bug.pending:
        age = f", {p.days} days" if p.days is not None else ""
        where = f"{p.package} {p.version} in {p.series}-proposed{age}"
        for name in p.flags:
            if name in _PENDING_FLAGS:
                flag(_PENDING_FLAGS[name][0], f"pending-sru: {where}: {_PENDING_FLAGS[name][1]}")
        if p.builds_not_ok:
            flag("attention", f"pending-sru: {where}: builds not OK ({', '.join(p.builds_not_ok)})")
        if not p.flags:
            found.append(("ready", f"pending-sru: {where}: awaiting verification"))
        task = next((s for s in bug.series if s.series == p.series), None)
        if task and task.pending:
            flag("review", f"LP task for {p.series} is '{task.status}' although {where}")

    devel_pending = any(s.series == "devel" for s in to_upload)
    if devel_pending and len(bug.series) > 1:
        flag("review", "Development release is not fixed yet")

    if not bug.has_sru_template:
        flag("attention", "SRU template ([Impact], [Test Plan], [Where problems could occur]) is incomplete")

    artifacts = [(p.title, p.series, p.lint) for p in bug.patches]
    artifacts += [(f"MP {mp.web_url}", [mp.series] if mp.series else [], mp.lint) for mp in bug.merge_proposals]
    if to_upload or not bug.series:
        if not artifacts:
            flag("attention", "No patches or merge proposals found for the pending series")
        else:
            unattributed = any(not series for _, series, _ in artifacts)
            covered = {s for _, series, _ in artifacts for s in series}
            if not unattributed:
                missing = [s.series for s in to_upload if s.series != "devel" and s.series not in covered]
                if missing:
                    flag("attention", f"No patch/MP identified for: {', '.join(missing)}")

    for name, _, lint in artifacts:
        if lint is None:
            continue
        if not lint.ran:
            flag("attention", f"sru-lint did not run on {name}: {lint.error}")
        elif lint.count("error"):
            flag("attention", f"sru-lint: {lint.count('error')} error(s) in {name}")
        elif lint.count("warning"):
            flag("review", f"sru-lint: {lint.count('warning')} warning(s) in {name}")

    for b in bug.blockers:
        if b.maybe_resolved:
            flag("review", f"Possibly resolved {b.severity}: {b.summary}")
        elif b.severity == "blocker":
            flag("blocked", b.summary)
        else:
            flag("attention", b.summary)

    linted = any(lint is not None for _, _, lint in artifacts)
    if not found:
        found.append(
            ("ready", "Patches pass sru-lint and no blockers detected" if linted else "No blockers detected (sru-lint not run)")
        )
    bug.level = level
    bug.attention_reasons = [r for _, r in sorted(found, key=lambda item: LEVELS.index(item[0]))]


def classify_issue(issue: IssueReview) -> None:
    if not issue.bugs:
        issue.level = "attention"
        issue.reasons = ["No Launchpad bug could be found in the Jira card"]
        return
    issue.level = _worst(*(b.level for b in issue.bugs))
    issue.reasons = [
        f"{b.label} ({b.package or '?'}): {r}" for b in issue.bugs if b.level == issue.level for r in b.attention_reasons
    ]
