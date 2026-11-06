"""Parser for the proposed-migration excuses page: why a package has not migrated out of -proposed yet."""

from __future__ import annotations

import html
import logging
import re
import threading

import requests

from sru_scout.http import make_session
from sru_scout.models import AutopkgtestResult, Excuse

log = logging.getLogger(__name__)

EXCUSES_URL = "https://ubuntu-archive-team.ubuntu.com/proposed-migration/{series}/update_excuses.html"

_ENTRY = re.compile(r'^<li><a id="([^"]+)"', re.M)
_TAG = re.compile(r"<[^>]+>")
_HEADER = re.compile(r"^\S+ \((.+?) to (.+?)\)")
_STATUS = re.compile(r"^Migration status for .*?\): (.*)$", re.S)
_DAYS = re.compile(r"^(\d+) days? old$")
_BLOCK_BUG = re.compile(r"Not touching package as requested in bug (\d+)")
_TEST = re.compile(r"^autopkgtest for ([^:]+):")
_TEST_RESULT = re.compile(r'<a href="[^"]*">([\w-]+)</a>: <a href="([^"]*)"><span[^>]*>([^<]+)</span>')
# Every stable SRU carries this hint until the SRU team releases it, and kernels always leave old binaries behind.
_NOISE = re.compile(r"^(Not touching package due to block request by freeze|old binaries left on)")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def split_entries(page: str) -> dict[str, str]:
    """Raw HTML of each top-level excuse, keyed by its anchor (the source package, or "pkg/arch")."""
    starts = [(m.start(), m.group(1)) for m in _ENTRY.finditer(page)]
    return {
        html.unescape(name): page[start : starts[i + 1][0] if i + 1 < len(starts) else len(page)]
        for i, (start, name) in enumerate(starts)
    }


def parse_entry(block: str, package: str, url: str = "") -> Excuse:
    _, header, *items = block.split("<li>")
    excuse = Excuse(series="", package=package, old_version="", new_version="", url=url)
    if m := _HEADER.match(_text(header)):
        excuse.old_version, excuse.new_version = m.groups()
    section = None
    for raw in items:
        text = _text(raw)
        if m := _STATUS.match(text):
            excuse.status = m.group(1)
        elif text == "Issues preventing migration:":
            section = excuse.issues
        elif text == "Additional info:":
            section = excuse.info
        elif m := _DAYS.match(text):
            excuse.days = int(m.group(1))
        elif m := _TEST.match(text):
            for arch, log_url, result in _TEST_RESULT.findall(raw):
                if result == "Regression":
                    excuse.regressions.append(AutopkgtestResult(m.group(1), arch, result, log_url))
                elif result.startswith("Test in progress"):
                    excuse.tests_running += 1
        elif m := _BLOCK_BUG.search(text):
            excuse.block_bugs.append(int(m.group(1)))
        elif section is not None and text and not _NOISE.match(text):
            section.append(text)
    return excuse


class ExcusesClient:
    """Fetches each series' excuses page at most once and looks packages up in it."""

    def __init__(self, url: str = EXCUSES_URL, session: requests.Session | None = None) -> None:
        self.url = url
        self.session = session or make_session()
        self._pages: dict[str, dict[str, str]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _entries(self, series: str) -> dict[str, str]:
        with self._lock:
            lock = self._locks.setdefault(series, threading.Lock())
        with lock:
            if series not in self._pages:
                try:
                    resp = self.session.get(self.url.format(series=series), timeout=120)
                    resp.raise_for_status()
                    self._pages[series] = split_entries(resp.text)
                except requests.RequestException as exc:  # the page is supplementary; carry on without it
                    log.warning("could not load excuses for %s: %s", series, exc)
                    self._pages[series] = {}
            return self._pages[series]

    def lookup(self, series: str, package: str) -> Excuse | None:
        block = self._entries(series).get(package)
        if block is None:
            return None
        excuse = parse_entry(block, package, f"{self.url.format(series=series)}#{package}")
        excuse.series = series
        return excuse
