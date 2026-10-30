from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from itertools import islice

from rich.console import Console

from sru_scout import report_cli
from sru_scout.agent import SruAgent
from sru_scout.jira import DEFAULT_BASE_URL, DEFAULT_BOARD, DEFAULT_JQL, JiraClient, JiraError, JiraIssue
from sru_scout.launchpad import LaunchpadClient
from sru_scout.lint import SruLint
from sru_scout.llm import LLMAnalyzer
from sru_scout.models import LEVELS, IssueReview
from sru_scout.pending import REPORT_URL, PendingSRU
from sru_scout.report_html import render_html


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sru-scout", description="Triage Ubuntu SRU Jira cards against Launchpad.")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("review", help="review Jira board cards (or specific bugs) and print a report")
    src = r.add_argument_group("what to review")
    src.add_argument("--board", type=int, default=DEFAULT_BOARD, help="Jira board id (default: %(default)s)")
    src.add_argument("--jql", default=DEFAULT_JQL, help="extra JQL applied to the board (default: %(default)r)")
    src.add_argument("--issue", action="append", default=[], metavar="KEY", help="review only this Jira key (repeatable)")
    src.add_argument("--bug", action="append", default=[], type=int, metavar="ID", help="review a Launchpad bug directly, no Jira needed (repeatable)")
    src.add_argument("--limit", type=int, help="maximum number of board cards (with a Launchpad bug) to review")
    src.add_argument("--jira-url", default=os.environ.get("JIRA_URL", DEFAULT_BASE_URL))
    chk = r.add_argument_group("analysis")
    chk.add_argument("--no-lint", action="store_true", help="skip running sru-lint")
    chk.add_argument("--sru-lint", default="sru-lint", help="sru-lint executable (default: %(default)s)")
    chk.add_argument("--max-patches", type=int, default=5, help="lint at most the newest N patches per bug")
    chk.add_argument("--no-pending-sru", action="store_true", help="do not consult the pending-SRU report")
    chk.add_argument("--pending-sru-url", default=REPORT_URL, help="pending-SRU report location (default: %(default)s)")
    chk.add_argument("--llm", action="store_true", help="also analyse comments with an LLM (needs SRU_SCOUT_LLM_URL and SRU_SCOUT_LLM_MODEL)")
    chk.add_argument("--workers", type=int, default=4, help="cards reviewed in parallel")
    out = r.add_argument_group("output")
    out.add_argument("--html", metavar="FILE", help="write an HTML report")
    out.add_argument("--json", metavar="FILE", help="write the raw review as JSON")
    out.add_argument("--fail-on-attention", action="store_true", help="exit 1 if any card is blocked or needs attention")
    out.add_argument("-v", "--verbose", action="store_true", help="show finished cards and lint findings in the console")
    return p


def _bug_issues(ids: list[int]) -> list[JiraIssue]:
    return [JiraIssue(f"LP#{i}", f"https://bugs.launchpad.net/bugs/{i}", f"LP: #{i}", texts={"summary": f"LP: #{i}"}) for i in ids]


def _collect_issues(args: argparse.Namespace) -> list[JiraIssue]:
    if args.bug and not args.issue:
        return _bug_issues(args.bug)
    email, token = os.environ.get("JIRA_EMAIL"), os.environ.get("JIRA_API_TOKEN")
    if not email or not token:
        raise SystemExit(
            "Set JIRA_EMAIL and JIRA_API_TOKEN (https://id.atlassian.com/manage-profile/security/api-tokens), "
            "or use --bug to review Launchpad bugs without Jira."
        )
    jira = JiraClient(email, token, args.jira_url)
    # Cards without a Launchpad bug are out of scope, so they don't count towards --limit either.
    if args.issue:
        issues = [i for i in map(jira.issue, args.issue) if i.bug_ids()]
    else:
        issues = list(islice((i for i in jira.board_issues(args.board, args.jql) if i.bug_ids()), args.limit))
    return issues + _bug_issues(args.bug)


def review(args: argparse.Namespace) -> int:
    console = Console(stderr=True)
    linter = None
    if not args.no_lint:
        linter = SruLint(args.sru_lint)
        if not linter.available():
            console.print(f"[red]'{args.sru_lint}' not found; install it (snap install --edge sru-lint) or use --no-lint[/red]")
            return 2
    llm = None
    if args.llm:
        llm = LLMAnalyzer.from_env()
        if llm is None:
            console.print("[red]--llm needs SRU_SCOUT_LLM_URL and SRU_SCOUT_LLM_MODEL (optionally SRU_SCOUT_LLM_API_KEY)[/red]")
            return 2

    try:
        issues = _collect_issues(args)
    except JiraError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    if not issues:
        console.print("No cards matched.")
        return 0

    pending = None
    if not args.no_pending_sru:
        try:
            pending = PendingSRU.fetch(args.pending_sru_url)
            console.print(f"pending-sru report: {len(pending)} bugs in -proposed")
        except Exception as exc:  # the report is supplementary; carry on without it
            console.print(f"[yellow]Could not load the pending-SRU report ({exc}); continuing without it[/yellow]")

    agent = SruAgent(LaunchpadClient(), linter, llm, pending, workers=args.workers, max_patches=args.max_patches)
    done = 0

    def progress(r: IssueReview) -> None:
        nonlocal done
        done += 1
        console.print(report_cli.progress_line(done, len(issues), r))

    reviews = agent.review_issues(issues, progress)
    for r in reviews:
        if r.key.startswith("LP#") and r.bugs and r.bugs[0].title:
            r.summary = r.bugs[0].title

    report_cli.render(reviews, Console(), verbose=args.verbose)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(reviews))
        console.print(f"HTML report: {args.html}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([dataclasses.asdict(r) for r in reviews], fh, indent=2)
        console.print(f"JSON report: {args.json}")
    if args.fail_on_attention and any(r.level in LEVELS[:3] for r in reviews):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return review(args)


if __name__ == "__main__":
    sys.exit(main())
