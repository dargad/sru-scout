"""Turns collected facts into an attention level plus ordered, linked findings."""

from __future__ import annotations

from sru_scout.models import LEVELS, BugReview, Finding, IssueReview
from sru_scout.pending import REPORT_URL

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

# Tag-based blockers already covered by the corresponding pending-sru flag: (flag, blocker summary prefix)
_TAG_DUPLICATES = (("verification-failed", "Verification failed"), ("block-proposed", "Release blocked by SRU team"))


def _worst(*levels: str) -> str:
    return min(levels, key=LEVELS.index)


def classify_bug(bug: BugReview) -> None:
    """Fill bug.level, bug.findings (worst first) and bug.attention_reasons in place."""
    found: list[Finding] = []
    level = "ready"

    def flag(new_level: str, text: str, url: str = "") -> None:
        nonlocal level
        level = _worst(level, new_level)
        found.append(Finding(new_level, text, url))

    def finish() -> None:
        bug.level = level
        bug.findings = sorted(found, key=lambda f: LEVELS.index(f.level))  # stable: keeps discovery order
        bug.attention_reasons = [f.text for f in bug.findings]

    if not bug.accessible:
        flag("attention", bug.error or "Launchpad bug is not accessible (private?)", bug.url)
        finish()
        return

    uploaded = {p.series for p in bug.pending}
    pending = [s for s in bug.series if s.pending]
    to_upload = [s for s in pending if s.series not in uploaded]
    if bug.series and not pending and not bug.pending:
        found.append(Finding("done", "All series are Fix Committed/Released or closed", bug.url))
        level = "done"
        finish()
        return
    if not bug.series:
        flag("attention", "Bug has no Ubuntu package tasks", bug.url)

    for p in bug.pending:
        age = f", {p.days} days" if p.days is not None else ""
        where = f"{p.package} {p.version} in {p.series}-proposed{age}"
        report_url = f"{REPORT_URL}#{p.series}"
        for name in p.flags:
            if name in _PENDING_FLAGS:
                flag(_PENDING_FLAGS[name][0], f"{where}: {_PENDING_FLAGS[name][1]}", report_url)
        if p.builds_not_ok:
            flag("attention", f"{where}: builds not OK ({', '.join(p.builds_not_ok)})", report_url)
        if not p.flags:
            found.append(Finding("ready", f"{where}: awaiting verification", report_url))
        task = next((s for s in bug.series if s.series == p.series), None)
        if task and task.pending:
            flag("review", f"LP task for {p.series} is '{task.status}' although {where}", task.url)

    if any(s.series == "devel" for s in to_upload) and len(bug.series) > 1:
        flag("review", "Development release is not fixed yet", bug.url)

    if not bug.has_sru_template:
        flag("attention", "SRU template ([Impact], [Test Plan], [Where problems could occur]) is incomplete", bug.url)

    artifacts = [(p.title, p.series, p.lint, p.web_url) for p in bug.patches]
    artifacts += [
        (f"merge proposal {mp.web_url.rsplit('/', 1)[-1]} ({mp.target.replace('refs/heads/', '') or '?'})", [mp.series] if mp.series else [], mp.lint, mp.web_url)
        for mp in bug.merge_proposals
    ]
    if to_upload or not bug.series:
        if not artifacts:
            flag("attention", "No patches or merge proposals found for the pending series", bug.url)
        else:
            unattributed = any(not series for _, series, _, _ in artifacts)
            covered = {s for _, series, _, _ in artifacts for s in series}
            if not unattributed:
                missing = [s.series for s in to_upload if s.series != "devel" and s.series not in covered]
                if missing:
                    flag("attention", f"No patch/MP identified for: {', '.join(missing)}", bug.url)

    for name, _, lint, url in artifacts:
        if lint is None:
            continue
        if not lint.ran:
            flag("attention", f"sru-lint did not run on {name}: {lint.error}", url)
        elif lint.count("error"):
            flag("attention", f"sru-lint: {lint.count('error')} error(s) in {name}", url)
        elif lint.count("warning"):
            flag("review", f"sru-lint: {lint.count('warning')} warning(s) in {name}", url)

    reported_by_pending = {f for p in bug.pending for f in p.flags}
    resolved = [b for b in bug.blockers if b.maybe_resolved]
    for b in bug.blockers:
        if b.maybe_resolved:
            continue
        if b.source == "tag" and any(
            flag_name in reported_by_pending and b.summary.startswith(prefix) for flag_name, prefix in _TAG_DUPLICATES
        ):
            continue
        flag("blocked" if b.severity == "blocker" else "attention", b.summary, b.url)
    if resolved:
        flag(
            "review",
            f"{len(resolved)} earlier concern(s) in comments look resolved by later comments; skim to confirm",
            resolved[0].url,
        )

    if not found:
        linted = any(lint is not None for _, _, lint, _ in artifacts)
        found.append(
            Finding(
                "ready",
                "Patches pass sru-lint and no blockers detected" if linted else "No blockers detected (sru-lint not run)",
            )
        )
    finish()


def classify_issue(issue: IssueReview) -> None:
    if not issue.bugs:
        issue.level = "attention"
        issue.reasons = ["No Launchpad bug could be found in the Jira card"]
        return
    issue.level = _worst(*(b.level for b in issue.bugs))
    issue.reasons = [
        f"{b.label} ({b.package or '?'}): {r}" for b in issue.bugs if b.level == issue.level for r in b.attention_reasons
    ]
