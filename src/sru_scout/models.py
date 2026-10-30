"""Plain data containers shared by all layers; all JSON-serialisable via dataclasses.asdict."""

from __future__ import annotations

from dataclasses import dataclass, field

# Ordered from worst to best; used for sorting and the report summary.
LEVELS = ("blocked", "attention", "review", "ready", "done")

# Launchpad bug task statuses
FIXED_STATUSES = {"Fix Committed", "Fix Released"}
CLOSED_STATUSES = {"Invalid", "Won't Fix", "Opinion", "Expired"}


@dataclass
class LintFinding:
    rule_id: str
    severity: str  # error | warning | info
    message: str
    path: str = ""
    line: int = 0
    doc_url: str = ""


@dataclass
class LintResult:
    url: str
    ran: bool = True
    error: str = ""  # set when sru-lint could not be run or its output not parsed
    findings: list[LintFinding] = field(default_factory=list)

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)


@dataclass
class Comment:
    author: str
    date: str
    body: str
    source: str  # "bug" | "mp"
    url: str = ""
    vote: str = ""  # MP review vote, if any


@dataclass
class Blocker:
    severity: str  # blocker | concern
    summary: str
    source: str  # rule/tag/mp/llm
    url: str = ""
    maybe_resolved: bool = False


@dataclass
class SeriesStatus:
    series: str  # e.g. "noble"; "devel" for the generic Ubuntu task
    status: str
    importance: str = ""
    assignee: str = ""
    url: str = ""
    eol: bool = False  # series is no longer supported, so a pending task is irrelevant

    @property
    def fixed(self) -> bool:
        return self.status in FIXED_STATUSES

    @property
    def closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def pending(self) -> bool:
        return not (self.fixed or self.closed or self.eol)


@dataclass
class Patch:
    title: str
    web_url: str
    data_url: str
    series: list[str] = field(default_factory=list)  # best-effort guess, empty if unknown
    lint: LintResult | None = None


@dataclass
class MergeProposal:
    web_url: str
    status: str
    source: str = ""
    target: str = ""
    series: str = ""
    diff_url: str = ""
    votes: list[str] = field(default_factory=list)  # "reviewer: Vote"
    comments: list[Comment] = field(default_factory=list)
    lint: LintResult | None = None


@dataclass
class PendingEntry:
    """A bug's row in the pending-SRU report: the fix sits in -proposed awaiting verification/release."""

    bug_id: int
    series: str
    package: str
    version: str
    days: int | None = None  # days since accepted into -proposed
    uploader: str = ""
    builds_not_ok: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """One reason a bug needs (or does not need) attention, with the page that backs it up."""

    level: str  # one of LEVELS
    text: str
    url: str = ""


@dataclass
class BugReview:
    bug_id: int
    url: str
    title: str = ""
    package: str = ""
    accessible: bool = True
    error: str = ""
    tags: list[str] = field(default_factory=list)
    has_sru_template: bool = True
    series: list[SeriesStatus] = field(default_factory=list)
    pending: list[PendingEntry] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
    merge_proposals: list[MergeProposal] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    attention_reasons: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    level: str = "attention"

    @property
    def label(self) -> str:
        return f"LP: #{self.bug_id}"

    def short_title(self, limit: int = 70) -> str:
        title = " ".join(self.title.split())
        return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


@dataclass
class IssueReview:
    key: str
    url: str
    summary: str
    status: str = ""
    assignee: str = ""
    bugs: list[BugReview] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    level: str = "attention"
