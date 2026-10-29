from sru_scout.models import Blocker, BugReview, IssueReview, LintFinding, LintResult, MergeProposal, Patch, SeriesStatus
from sru_scout.report_html import render_html
from sru_scout.triage import classify_bug, classify_issue

def bug(series, patches=(), mps=(), blockers=(), **kw):
    b = BugReview(1, "https://bugs.launchpad.net/bugs/1", series=list(series), patches=list(patches),
                  merge_proposals=list(mps), blockers=list(blockers), **kw)
    classify_bug(b)
    return b


def lint(*sev):
    return LintResult("u", findings=[LintFinding("R", s, "m") for s in sev])


def test_all_fixed_is_done():
    assert bug([SeriesStatus("noble", "Fix Released"), SeriesStatus("devel", "Fix Committed")]).level == "done"


def test_eol_pending_is_ignored():
    assert bug([SeriesStatus("focal", "Triaged", eol=True)]).level == "done"


def test_no_artifacts_needs_attention():
    b = bug([SeriesStatus("noble", "In Progress")])
    assert b.level == "attention" and "No patches" in b.attention_reasons[0]


def test_clean_lint_is_ready():
    p = Patch("fix", "w", "d", [], lint("info"))
    assert bug([SeriesStatus("noble", "In Progress")], [p]).level == "ready"


def test_lint_levels():
    series = [SeriesStatus("noble", "In Progress")]
    assert bug(series, [Patch("p", "w", "d", [], lint("warning"))]).level == "review"
    assert bug(series, [Patch("p", "w", "d", [], lint("error"))]).level == "attention"
    failed = LintResult("u", ran=False, error="boom")
    assert bug(series, [Patch("p", "w", "d", [], failed)]).level == "attention"


def test_blocker_beats_everything():
    b = bug([SeriesStatus("noble", "In Progress")], [Patch("p", "w", "d", [], lint("error"))], blockers=[Blocker("blocker", "nack", "comment")])
    assert b.level == "blocked"


def test_resolved_blocker_downgraded_to_review():
    p = Patch("p", "w", "d", [], lint())
    b = bug([SeriesStatus("noble", "In Progress")], [p], blockers=[Blocker("blocker", "nack", "comment", maybe_resolved=True)])
    assert b.level == "review"


def test_missing_series_coverage():
    series = [SeriesStatus("noble", "In Progress"), SeriesStatus("jammy", "In Progress")]
    attributed = bug(series, [Patch("p", "w", "d", ["noble"], lint())])
    assert attributed.level == "attention" and "jammy" in attributed.attention_reasons[0]
    unattributed = bug(series, [Patch("p", "w", "d", [], lint())])
    assert unattributed.level == "ready"
    mp = bug(series, [Patch("p", "w", "d", ["noble"], lint())], [MergeProposal("m", "Needs review", series="jammy", lint=lint())])
    assert mp.level == "ready"


def test_private_bug():
    assert bug([], accessible=False, error="private").level == "attention"


def test_issue_level_and_no_bug():
    issue = IssueReview("SI-1", "u", "s")
    classify_issue(issue)
    assert issue.level == "attention"
    issue.bugs = [bug([SeriesStatus("noble", "Fix Released")]), bug([SeriesStatus("noble", "In Progress")])]
    classify_issue(issue)
    assert issue.level == "attention"


def test_html_renders_and_escapes():
    p = Patch("<script>alert(1)</script>", "javascript:alert(1)", "d", ["noble"], lint("error"))
    b = bug([SeriesStatus("noble", "In Progress")], [p], mps=[MergeProposal("https://code.launchpad.net/m", "Needs review", votes=["a: Approve"])])
    issue = IssueReview("SI-1", "https://jira/SI-1", "Summary <b>", bugs=[b])
    classify_issue(issue)
    html = render_html([issue])
    assert "<script>alert(1)" not in html
    assert 'href="javascript:' not in html
    assert "SI-1" in html and "sru-lint" in html
    assert "LP: #1" in html


def test_label_title_and_issue_reason_prefix():
    b = bug([SeriesStatus("noble", "In Progress")], package="foo", title="x" * 100)
    assert b.label == "LP: #1"
    assert len(b.short_title(20)) == 20 and b.short_title(20).endswith("…")
    issue = IssueReview("SI-1", "u", "s", bugs=[b])
    classify_issue(issue)
    assert all(r.startswith("LP: #1 (foo): ") for r in issue.reasons)


def test_progress_line_has_bug_number_package_and_title():
    from sru_scout.report_cli import progress_line

    b = bug([SeriesStatus("noble", "In Progress")], package="foo", title="[SRU] fix the thing")
    issue = IssueReview("SI-9", "u", "card", bugs=[b])
    classify_issue(issue)
    text = progress_line(3, 43, issue).plain
    assert text.startswith("[3/43] SI-9 ATTENTION")
    assert "LP: #1 (foo) [SRU] fix the thing" in text
    empty = IssueReview("SI-10", "u", "card summary")
    classify_issue(empty)
    assert "no Launchpad bug found" in progress_line(1, 1, empty).plain


def test_progress_line_description_only_when_not_done():
    from sru_scout.models import Blocker
    from sru_scout.report_cli import progress_line

    done = bug([SeriesStatus("noble", "Fix Released")], package="foo", title="t")
    issue = IssueReview("SI-1", "u", "s", bugs=[done])
    classify_issue(issue)
    assert "->" not in progress_line(1, 1, issue).plain

    p = Patch("p", "w", "d", [], lint("warning"))
    blocked = bug([SeriesStatus("noble", "In Progress")], [p], blockers=[Blocker("blocker", "NACK from reviewer", "comment")])
    issue = IssueReview("SI-2", "u", "s", bugs=[blocked])
    classify_issue(issue)
    # The worst reason comes first even though the lint warning was found earlier.
    assert "-> NACK from reviewer" in progress_line(1, 1, issue).plain
