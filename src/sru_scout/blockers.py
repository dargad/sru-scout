"""Rule-based detection of release blockers in bug tags, bug comments and merge proposal reviews."""

from __future__ import annotations

import re

from sru_scout.models import Blocker, BugReview, Comment

BOT_AUTHORS = {"ubuntu-archive-robot", "launchpad", "janitor", "bug-importer", "ubuntu-sponsoring-bot"}

SRU_TEMPLATE_SECTIONS = ("impact", "test plan", "where problems could occur")

_TAG_RULES = (
    (re.compile(r"^verification-failed(-.+)?$"), "blocker", "Verification failed ({tag})"),
    (re.compile(r"^regression-(update|proposed|release)$"), "blocker", "Regression reported ({tag})"),
    (re.compile(r"^block-proposed(-.+)?$"), "blocker", "Release blocked by SRU team ({tag})"),
    (re.compile(r"^(sru-)?(needs|blocked).*"), "concern", "Tag '{tag}' set"),
)

# (pattern, severity, summary). A strong signal blocks the release; a soft one needs a human look.
_TEXT_RULES = (
    (r"\b(nack|nak)\b|\bdisapprove|\bdo not (upload|release|accept|promote)\b|\bnot (ready|acceptable) (for|to)\b", "blocker", "Reviewer objects to the change"),
    (r"\bFTBFS\b|\bfail(s|ed|ing)? to build\b", "blocker", "Build failure reported"),
    (r"autopkgtests?\b.{0,60}\b(fail|regress)|\b(fail|regress)\w*\b.{0,60}autopkgtests?\b", "blocker", "autopkgtest failure/regression reported"),
    (r"\bregress(ion|ed|es)\b(?! potential)", "concern", "Regression mentioned"),
    (r"\bblock(er|ed|s|ing)\b", "concern", "Blocker mentioned"),
    (r"\bneeds? (fixing|work|changes|rework|to be (fixed|updated|rebased))\b|\bplease (fix|update|add|rebase|drop|resubmit|rework)\b", "concern", "Reviewer requests changes"),
    (r"\bmissing\b.{0,40}\b(test plan|sru template|changelog|bug (number|reference)|dep-?3|autopkgtest|ppa)\b", "concern", "Missing required information"),
    (r"\b(verification|testing).{0,30}\bfail", "blocker", "Verification failure reported"),
)
_COMPILED_TEXT_RULES = [(re.compile(p, re.I | re.S), sev, text) for p, sev, text in _TEXT_RULES]

_RESOLVED = re.compile(
    r"\b(fixed|resolved|addressed|done|updated|uploaded|pushed|rebased|new (version|debdiff|patch)|lgtm|approved?)\b", re.I
)
_BAD_VOTES = {"Disapprove": "blocker", "Needs Fixing": "concern", "Needs Information": "concern", "Resubmit": "concern", "Abstain": ""}
_BAD_MP_STATUS = {"Rejected": "blocker", "Work in progress": "concern"}

# Standard SRU-team messages (acceptance, verification, release notices) mention regressions and fixes by design.
_BOILERPLATE = re.compile(
    r"Accepted \S+ into \S+-proposed|The verification of the Stable Release Update for|This bug was fixed in the package",
    re.I,
)


def has_sru_template(description: str) -> bool:
    text = description.lower()
    return all(re.search(rf"\[\s*{re.escape(section)}\s*\]", text) for section in SRU_TEMPLATE_SECTIONS)


def _strip_quotes(body: str) -> str:
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))


def analyze_tags(tags: list[str], bug_url: str) -> list[Blocker]:
    found = []
    for tag in tags:
        for pattern, severity, summary in _TAG_RULES:
            if pattern.match(tag):
                found.append(Blocker(severity, summary.format(tag=tag), "tag", bug_url))
                break
    return found


def _is_noise(comment: Comment) -> bool:
    return comment.author in BOT_AUTHORS or bool(_BOILERPLATE.search(comment.body))


def analyze_comments(comments: list[Comment]) -> list[Blocker]:
    """Flag comments matching blocker patterns; mark them resolved when a later comment says so."""
    found: list[Blocker] = []
    for i, comment in enumerate(comments):
        if _is_noise(comment):
            continue
        if comment.source == "bug" and i == 0:  # first message is the bug description
            continue
        body = _strip_quotes(comment.body)
        for pattern, severity, summary in _COMPILED_TEXT_RULES:
            m = pattern.search(body)
            if not m:
                continue
            snippet = " ".join(body[max(0, m.start() - 40) : m.end() + 80].split())
            later = comments[i + 1 :]
            resolved = any(_RESOLVED.search(_strip_quotes(c.body)) for c in later if not _is_noise(c))
            found.append(
                Blocker(severity, f"{summary}: \"…{snippet}…\" ({comment.author})", "comment", comment.url, resolved)
            )
            break  # one finding per comment is enough for triage
        if comment.vote and _BAD_VOTES.get(comment.vote):
            found.append(
                Blocker(_BAD_VOTES[comment.vote], f"{comment.author} voted '{comment.vote}'", "mp-vote", comment.url)
            )
    return found


def analyze_bug(bug: BugReview, comments: list[Comment]) -> list[Blocker]:
    result = analyze_tags(bug.tags, bug.url)
    result += analyze_comments(comments)
    for mp in bug.merge_proposals:
        severity = _BAD_MP_STATUS.get(mp.status)
        if severity:
            result.append(Blocker(severity, f"Merge proposal status is '{mp.status}'", "mp", mp.web_url))
        for b in analyze_comments(mp.comments):
            b.url = b.url or mp.web_url
            result.append(b)
    unique: dict[tuple[str, str], Blocker] = {}
    for b in result:
        unique.setdefault((b.severity, b.summary), b)
    return list(unique.values())
