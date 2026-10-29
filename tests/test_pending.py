from sru_scout.models import BugReview, PendingEntry, SeriesStatus
from sru_scout.pending import PendingSRU, parse_report
from sru_scout.triage import classify_bug

PAGE = """
<h3>noble</h3>
<table id='noble'>
  <tr><th>Package</th><th>-release</th><th>-updates</th><th>-proposed</th><th>changelog bugs</th><th>days</th></tr>
  <tr><td><a href="https://launchpad.net/ubuntu/+source/atlas/">atlas</a><span style="font-size: x-small"><br/>amd64: <a href="https://x/+build/1">Failed to build</a> </span>  </td>
  <td><a href="https://x/1">3.10-1</a></td>
  <td><a href="https://x/"></a></td>
  <td><a href="https://x/3.10-1.1">3.10-1.1</a> (<a href="https://launchpad.net/~doko">doko</a>)</td>
  <td>
<a href="https://launchpad.net/bugs/111" title="SRU: a &quot;title&quot;
multi-line"  class=" verified blockproposed">111</a>
<a href="https://launchpad.net/bugs/222" title="t"  class=" verificationfailed">222</a>
&nbsp;</td>
  <td>12</td>
  </tr>
</table>
<table class="noborder"><tr><td>ignored</td></tr></table>
"""


def test_parse_report():
    entries = parse_report(PAGE)
    assert [(e.bug_id, e.series, e.package, e.version, e.days, e.uploader) for e in entries] == [
        (111, "noble", "atlas", "3.10-1.1", 12, "doko"),
        (222, "noble", "atlas", "3.10-1.1", 12, "doko"),
    ]
    assert entries[0].flags == ["block-proposed", "verified"]
    assert entries[1].flags == ["verification-failed"]
    assert entries[0].builds_not_ok == ["amd64: Failed to build"]


def test_lookup():
    pending = PendingSRU(parse_report(PAGE))
    assert len(pending) == 2 and pending.for_bug(999) == []


def bug(entry_flags, task_status="Fix Committed", builds=()):
    b = BugReview(1, "u", series=[SeriesStatus("noble", task_status)],
                  pending=[PendingEntry(1, "noble", "pkg", "1.0", 9, flags=list(entry_flags), builds_not_ok=list(builds))])
    classify_bug(b)
    return b


def test_fixed_but_in_proposed_is_not_done():
    assert bug([]).level == "ready"
    assert bug(["verified"]).level == "review"
    assert bug(["incomplete"]).level == "attention"
    assert bug(["verification-failed"]).level == "blocked"
    assert bug(["verified", "block-proposed"]).level == "blocked"
    assert bug([], builds=["amd64: Failed to build"]).level == "attention"


def test_proposed_needs_no_patches_even_if_task_not_fixed():
    b = bug([], task_status="In Progress")
    assert b.level == "review"
    assert not any("No patches" in r for r in b.attention_reasons)
    assert any("although" in r for r in b.attention_reasons)
