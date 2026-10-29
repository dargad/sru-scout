from sru_scout.blockers import analyze_comments, analyze_tags, has_sru_template
from sru_scout.jira import JiraIssue, adf_to_text, extract_bug_ids
from sru_scout.launchpad import UbuntuSeries, guess_series
from sru_scout.lint import parse_findings
from sru_scout.llm import parse_reply
from sru_scout.models import Comment

SERIES = [UbuntuSeries("jammy", "22.04", "Supported"), UbuntuSeries("noble", "24.04", "Current Stable Release")]


def test_extract_bug_ids_forms():
    text = "SRU LP: #123 see https://bugs.launchpad.net/ubuntu/+source/foo/+bug/456 and https://launchpad.net/bugs/789 LP#123"
    assert extract_bug_ids(text) == [123, 456, 789]


def test_issue_prefers_summary_over_comments():
    issue = JiraIssue("SI-1", "u", "s", texts={"summary": "pkg (LP: #1)", "comments": "dup of LP: #2"})
    assert issue.bug_ids() == [1]
    assert JiraIssue("SI-2", "u", "s", texts={"comments": "LP: #2"}).bug_ids() == [2]


def test_adf_keeps_links():
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "bug", "marks": [{"type": "link", "attrs": {"href": "https://bugs.launchpad.net/bugs/42"}}]}]}]}
    assert extract_bug_ids(adf_to_text(adf)) == [42]


def test_guess_series_by_name_and_version():
    assert guess_series("debdiff for Noble", SERIES) == ["noble"]
    assert guess_series("fix_24.04.diff and jammy", SERIES) == ["noble", "jammy"]
    assert guess_series("against 1.0.7-6ubuntu0.1", SERIES) == []
    assert guess_series("version 1.22.04.3", SERIES) == []


def test_parse_findings_tolerates_preamble():
    out = 'noise\n[{"message": "m", "rule_id": "X1", "severity": "error", "span": {"path": "a", "start_line": 3}}]\n'
    (f,) = parse_findings(out)
    assert (f.rule_id, f.severity, f.path, f.line) == ("X1", "error", "a", 3)


def test_parse_findings_rejects_garbage():
    try:
        parse_findings("Traceback...")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_sru_template():
    assert has_sru_template("[ Impact ]\nx\n[ Test Plan ]\ny\n[ Where problems could occur ]\nz")
    assert not has_sru_template("[ Impact ]\nonly")


def test_tags():
    found = analyze_tags(["verification-needed-noble", "verification-failed-noble", "block-proposed-jammy"], "u")
    assert [b.severity for b in found] == ["blocker", "blocker"]


def c(author, body, i=0, **kw):
    return Comment(author, "d", body, "bug", f"u{i}", **kw)


def test_comment_blocker_and_resolution():
    comments = [c("desc", "bug text"), c("sil2100", "NACK, this regresses ssh login", 1), c("dev", "Fixed, uploaded new debdiff", 2)]
    found = analyze_comments(comments)
    assert found and found[0].severity == "blocker" and found[0].maybe_resolved


def test_comment_ignores_description_bots_and_quotes():
    comments = [
        c("desc", "this is a regression"),
        c("ubuntu-archive-robot", "FTBFS everywhere", 1),
        c("a", "> please fix this\nlooks fine", 2),
        c("b", "Regression Potential: low", 3),
    ]
    assert analyze_comments(comments) == []


def test_mp_vote():
    found = analyze_comments([Comment("r", "d", "ok", "mp", "u", vote="Disapprove")])
    assert [b.severity for b in found] == ["blocker"]


def test_llm_reply_parsing():
    reply = 'Sure:\n[{"severity": "blocker", "summary": "x", "comment_url": ""}, {"summary": "y"}, "junk"]'
    result = parse_reply(reply, "https://bug")
    assert [(b.severity, b.url) for b in result] == [("blocker", "https://bug"), ("concern", "https://bug")]
    assert parse_reply("no json", "u") == []
