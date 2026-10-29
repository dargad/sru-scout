"""Orchestrates Jira -> Launchpad -> sru-lint -> blocker analysis for SRU cards."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from sru_scout import blockers
from sru_scout.jira import JiraIssue
from sru_scout.launchpad import LaunchpadClient, LaunchpadError
from sru_scout.lint import SruLint
from sru_scout.llm import LLMAnalyzer
from sru_scout.models import BugReview, IssueReview
from sru_scout.pending import PendingSRU
from sru_scout.triage import classify_bug, classify_issue

log = logging.getLogger(__name__)


class SruAgent:
    def __init__(
        self,
        launchpad: LaunchpadClient,
        linter: SruLint | None = None,
        llm: LLMAnalyzer | None = None,
        pending: PendingSRU | None = None,
        workers: int = 4,
        max_patches: int = 5,
    ) -> None:
        self.lp = launchpad
        self.linter = linter
        self.llm = llm
        self.pending = pending
        self.workers = workers
        self.max_patches = max_patches

    def review_bug(self, bug_id: int) -> BugReview:
        review = BugReview(bug_id=bug_id, url=f"https://bugs.launchpad.net/bugs/{bug_id}")
        try:
            self._collect(review)
        except LaunchpadError as exc:
            review.accessible = exc.accessible
            review.error = str(exc)
        except Exception as exc:  # one broken bug must not abort the whole run
            log.exception("review of LP #%s failed", bug_id)
            review.accessible = False
            review.error = f"Unexpected error: {exc}"
        classify_bug(review)
        return review

    def _collect(self, review: BugReview) -> None:
        bug = self.lp.bug(review.bug_id)
        review.title = bug.get("title", "")
        review.tags = bug.get("tags", [])
        review.has_sru_template = blockers.has_sru_template(bug.get("description") or "")
        review.package, review.series = self.lp.series_statuses(bug)

        active = {s.name for s in self.lp.ubuntu_series() if s.active}
        for status in review.series:
            status.eol = status.series != "devel" and status.series not in active

        # Releases whose fix is in -proposed are already uploaded; patches matter only for the rest.
        review.pending = self.pending.for_bug(review.bug_id) if self.pending else []
        uploaded = {p.series for p in review.pending}
        needs_upload = not review.series or any(s.pending and s.series not in uploaded for s in review.series)
        if not needs_upload and not review.pending:
            return

        comments = self.lp.comments(bug)
        if needs_upload:
            review.patches = self.lp.patches(bug)[-self.max_patches :]
            review.merge_proposals = self.lp.merge_proposals(bug)
            if self.linter:
                self._lint(review)
        review.blockers = blockers.analyze_bug(review, comments)
        if self.llm:
            try:
                review.blockers += self.llm.analyze(review, comments)
            except Exception as exc:
                log.warning("LLM analysis of LP #%s failed: %s", review.bug_id, exc)

    def _lint(self, review: BugReview) -> None:
        assert self.linter
        targets = [(p, p.data_url) for p in review.patches]
        targets += [(mp, mp.diff_url) for mp in review.merge_proposals if mp.diff_url]
        for artifact, url in targets:
            artifact.lint = self.linter.check(url)

    def review_issue(self, issue: JiraIssue) -> IssueReview:
        review = IssueReview(
            key=issue.key, url=issue.url, summary=issue.summary, status=issue.status, assignee=issue.assignee
        )
        review.bugs = [self.review_bug(bug_id) for bug_id in issue.bug_ids()]
        classify_issue(review)
        return review

    def review_issues(
        self, issues: Iterable[JiraIssue], progress: Callable[[IssueReview], None] | None = None
    ) -> list[IssueReview]:
        reviews = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for review in pool.map(self.review_issue, list(issues)):
                reviews.append(review)
                if progress:
                    progress(review)
        return reviews
