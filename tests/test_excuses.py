from sru_scout.excuses import ExcusesClient, parse_entry, split_entries
from sru_scout.models import BugReview, Excuse, PendingEntry, SeriesStatus
from sru_scout.triage import classify_bug

PAGE = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">
<html><head><title>excuses...</title></head><body>
<p>Generated: 2026.11.06 09:22:14 +0000</p>
<ul>
<li><a id="systemd/riscv64" name="systemd/riscv64" href="https://launchpad.net/ubuntu/+source/systemd">systemd/riscv64</a> (<a href="x">1</a> to <a href="x">1</a>)
<ul>
<li>Migration status for systemd/riscv64 (1 to 1): BLOCKED: Needs an approval (either due to a freeze, the source suite or a manual hint)
</ul>
<li><a id="systemd" name="systemd" href="https://launchpad.net/ubuntu/+source/systemd">systemd</a> (<a href="https://launchpad.net/ubuntu/+source/systemd/255.4-1ubuntu8.18">255.4-1ubuntu8.18</a> to <a href="https://launchpad.net/ubuntu/+source/systemd/255.4-1ubuntu8.19">255.4-1ubuntu8.19</a>)
<ul>
<li>Migration status for systemd (255.4-1ubuntu8.18 to 255.4-1ubuntu8.19): BLOCKED: Rejected/violates migration policy/introduces a regression
<li>Issues preventing migration:
<li>autopkgtest for amavisd-new/unknown: <a href="https://autopkgtest.ubuntu.com/packages/a/amavisd-new/noble/armhf">armhf</a>: <a href="https://autopkgtest.ubuntu.com/results/a/log.gz"><span style="background:#ff6666">Regression</span></a> <a href="https://autopkgtest.ubuntu.com/request.cgi?x" style="text-decoration: none;">&#9851; </a> 
<li>autopkgtest for cron/3.0pl1-184ubuntu2: <a href="https://autopkgtest.ubuntu.com/packages/c/cron/noble/amd64">amd64</a>: <a href="https://autopkgtest.ubuntu.com/results/c/log.gz"><span style="background:#ff6666">Regression</span></a> <a href="x">&#9851; </a>, <a href="https://autopkgtest.ubuntu.com/packages/c/cron/noble/arm64">arm64</a>: <a href="https://autopkgtest.ubuntu.com/running"><span style="background:#99ddff">Test in progress</span></a>, <a href="https://autopkgtest.ubuntu.com/packages/c/cron/noble/s390x">s390x</a>: <a href="https://autopkgtest.ubuntu.com/results/c2/log.gz"><span style="background:#87d96c">Pass</span></a>
<li>missing build on <a href="https://launchpad.net/x">arm64</a>: libsystemd0 (from <a href="x">255.4-1ubuntu8.18</a>)
<li>Not touching package due to block request by freeze (please contact ubuntu-release if update is needed)
<li>Additional info:
<li>autopkgtest for apt/2.8.4ubuntu0.1: <a href="https://autopkgtest.ubuntu.com/packages/a/apt/noble/amd64">amd64</a>: <a href="https://autopkgtest.ubuntu.com/results/apt/log.gz"><span style="background:#87d96c">Pass</span></a>
<li>Depends: systemd apt (not considered)
<li>4 days old
<li>Not touching package as requested in <a href="https://launchpad.net/bugs/2167508">bug 2167508</a> on Thu Sep 24 17:03:32 2026
</ul>
</ul>
</body></html>
"""


def test_split_entries_keeps_arch_specific_entries_apart():
    entries = split_entries(PAGE)
    assert list(entries) == ["systemd/riscv64", "systemd"]
    assert "riscv64" not in entries["systemd"].split("\n", 1)[1]


def test_parse_entry():
    e = parse_entry(split_entries(PAGE)["systemd"], "systemd", "u#systemd")
    assert (e.old_version, e.new_version, e.days) == ("255.4-1ubuntu8.18", "255.4-1ubuntu8.19", 4)
    assert e.status == "BLOCKED: Rejected/violates migration policy/introduces a regression"
    assert [(r.test, r.arch, r.url) for r in e.regressions] == [
        ("amavisd-new/unknown", "armhf", "https://autopkgtest.ubuntu.com/results/a/log.gz"),
        ("cron/3.0pl1-184ubuntu2", "amd64", "https://autopkgtest.ubuntu.com/results/c/log.gz"),
    ]
    assert e.tests_running == 1
    assert e.issues == ["missing build on arm64: libsystemd0 (from 255.4-1ubuntu8.18)"]
    assert e.info == ["Depends: systemd apt (not considered)"]
    assert e.block_bugs == [2167508]


class FakeSession:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        resp = type("R", (), {"text": PAGE, "raise_for_status": lambda self: None})()
        return resp


def test_client_fetches_each_series_once():
    session = FakeSession()
    client = ExcusesClient("https://h/{series}/update_excuses.html", session)
    assert client.lookup("noble", "systemd").url == "https://h/noble/update_excuses.html#systemd"
    assert client.lookup("noble", "plymouth") is None
    assert session.urls == ["https://h/noble/update_excuses.html"]


def bug(**excuse):
    b = BugReview(1, "u", series=[SeriesStatus("noble", "Fix Committed")],
                  pending=[PendingEntry(1, "noble", "systemd", "2", 3)],
                  excuses=[Excuse("noble", "systemd", "1", "2", "u#systemd", **excuse)])
    classify_bug(b)
    return b


def test_excuse_classification():
    assert bug().level == "ready"
    assert bug(tests_running=3).level == "ready"
    assert bug(issues=["Not touching package due to block request by kernel-testing"]).level == "ready"
    assert bug(issues=["missing build on amd64: x"]).level == "engineer-attention"
    assert bug(block_bugs=[5]).level == "blocked"
    b = bug(regressions=[parse_entry(split_entries(PAGE)["systemd"], "systemd").regressions[0]])
    assert b.level == "engineer-attention"
    assert "autopkgtest regressions: amavisd-new/unknown (armhf)" in b.findings[0].text
