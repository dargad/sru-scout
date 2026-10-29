"""Parser for the Ubuntu archive team's pending-SRU report (packages in -proposed awaiting release)."""

from __future__ import annotations

import html
import re

import requests

from sru_scout.http import make_session
from sru_scout.models import PendingEntry

REPORT_URL = "https://static-reports.ubuntu.com/pending-sru/pending-sru.html"

_TABLE = re.compile(r"<table id='(\w+)'>(.*?)</table>", re.S)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td>(.*?)</td>", re.S)
_ANCHOR_TEXT = re.compile(r"<a [^>]*>([^<]+)</a>")
_BUG = re.compile(r'<a href="[^"]*/bugs/(\d+)"[^>]*?class="([^"]*)"[^>]*>', re.S)
_BUILD = re.compile(r'(\w+): <a href="[^"]*">([^<]+)</a>')
_USER = re.compile(r"launchpad\.net/~([\w.+-]+)")

# CSS class used by the report -> flag name used here
_FLAGS = {
    "verified": "verified",
    "verificationfailed": "verification-failed",
    "incomplete": "incomplete",
    "messages": "messages",
    "testing": "testing",
    "broken": "broken",
    "removal": "removal-candidate",
    "blockproposed": "block-proposed",
    "kerneltracking": "kernel-tracking",
}


def parse_report(page: str) -> list[PendingEntry]:
    """One entry per (series, package, bug) row in the report."""
    entries: list[PendingEntry] = []
    for series, table in _TABLE.findall(page):
        for row in _ROW.findall(table):
            cells = _CELL.findall(row)
            if len(cells) < 6:  # header rows use <th>
                continue
            package_cell, _release, _updates, proposed_cell, bugs_cell, days = cells[:6]
            package = _ANCHOR_TEXT.search(package_cell)
            version = _ANCHOR_TEXT.search(proposed_cell)
            if not package or not version:
                continue
            users = _USER.findall(proposed_cell)
            builds = [f"{arch}: {status}" for arch, status in _BUILD.findall(package_cell)]
            for bug_id, classes in _BUG.findall(bugs_cell):
                flags = sorted({_FLAGS[c] for c in classes.split() if c in _FLAGS})
                entries.append(
                    PendingEntry(
                        bug_id=int(bug_id),
                        series=series,
                        package=html.unescape(package.group(1)).strip(),
                        version=html.unescape(version.group(1)).strip(),
                        days=int(days.strip()) if days.strip().isdigit() else None,
                        uploader=users[0] if users else "",
                        builds_not_ok=builds,
                        flags=flags,
                    )
                )
    return entries


class PendingSRU:
    def __init__(self, entries: list[PendingEntry]) -> None:
        self._by_bug: dict[int, list[PendingEntry]] = {}
        for e in entries:
            self._by_bug.setdefault(e.bug_id, []).append(e)

    @classmethod
    def fetch(cls, url: str = REPORT_URL, session: requests.Session | None = None) -> PendingSRU:
        resp = (session or make_session()).get(url, timeout=60)
        resp.raise_for_status()
        return cls(parse_report(resp.text))

    def __len__(self) -> int:
        return len(self._by_bug)

    def for_bug(self, bug_id: int) -> list[PendingEntry]:
        return list(self._by_bug.get(bug_id, []))
