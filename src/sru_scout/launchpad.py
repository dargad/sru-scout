"""Anonymous Launchpad REST client covering just what an SRU review needs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

import requests

from sru_scout.http import TIMEOUT, make_session
from sru_scout.models import Comment, MergeProposal, Patch, SeriesStatus

API = "https://api.launchpad.net/devel"

_TASK_TARGET = re.compile(r"^(?P<pkg>\S+) \(Ubuntu(?: (?P<series>[A-Za-z]+))?\)$")
_PATCH_NAME = re.compile(r"\.(debdiff|diff|patch)$", re.I)


class LaunchpadError(RuntimeError):
    def __init__(self, message: str, accessible: bool = True) -> None:
        super().__init__(message)
        self.accessible = accessible


@dataclass
class UbuntuSeries:
    name: str
    version: str
    status: str  # Active Development | Current Stable Release | Supported | Obsolete | ...

    @property
    def active(self) -> bool:
        return self.status != "Obsolete"


def _name(link: str | None) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1].lstrip("~") if link else ""


def guess_series(text: str, known: list[UbuntuSeries]) -> list[str]:
    """Return series named (or version-numbered) in `text`, in order of appearance."""
    hits: list[tuple[int, str]] = []
    for s in known:
        for pattern in (rf"\b{re.escape(s.name)}\b", rf"(?<![\d.]){re.escape(s.version.split()[0])}(?:\.\d+)?(?![\d])"):
            m = re.search(pattern, text, re.I)
            if m:
                hits.append((m.start(), s.name))
                break
    return [name for _, name in sorted(hits)]


def annotate_series(statuses: list[SeriesStatus], known: list[UbuntuSeries]) -> None:
    """Fill in release name, version and EOL flag; the devel task stands for the release in development."""
    by_name = {s.name: s for s in known}
    devel = next((s for s in known if s.status in ("Active Development", "Pre-release Freeze")), None)
    for status in statuses:
        release = devel if status.series == "devel" else by_name.get(status.series)
        status.eol = status.series != "devel" and not (release and release.active)
        if release:
            status.codename, status.version = release.name, release.version


class LaunchpadClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or make_session()
        self.session.headers["Accept"] = "application/json"
        self._series: list[UbuntuSeries] | None = None

    def _get(self, url: str) -> Any:
        resp = self.session.get(url, timeout=TIMEOUT)
        if resp.status_code in (401, 403, 404):
            raise LaunchpadError(f"{url} not accessible anonymously ({resp.status_code})", accessible=False)
        if not resp.ok:
            raise LaunchpadError(f"{url}: HTTP {resp.status_code}")
        return resp.json()

    def _entries(self, url: str) -> Iterator[dict[str, Any]]:
        next_url: str | None = url
        while next_url:
            page = self._get(next_url)
            yield from page.get("entries", [])
            next_url = page.get("next_collection_link")

    def ubuntu_series(self) -> list[UbuntuSeries]:
        if self._series is None:
            self._series = [
                UbuntuSeries(e["name"], e["version"], e["status"]) for e in self._entries(f"{API}/ubuntu/series")
            ]
        return self._series

    def bug(self, bug_id: int) -> dict[str, Any]:
        return self._get(f"{API}/bugs/{bug_id}")

    def series_statuses(self, bug: dict[str, Any]) -> tuple[str, list[SeriesStatus]]:
        """Return (source package(s), per-series statuses) from the bug's Ubuntu tasks."""
        packages: list[str] = []
        result: list[SeriesStatus] = []
        for task in self._entries(bug["bug_tasks_collection_link"]):
            m = _TASK_TARGET.match(task.get("bug_target_name", ""))
            if not m:
                continue
            if m["pkg"] not in packages:
                packages.append(m["pkg"])
            result.append(
                SeriesStatus(
                    series=(m["series"] or "devel").lower(),
                    status=task["status"],
                    importance=task.get("importance", ""),
                    assignee=_name(task.get("assignee_link")),
                    url=task["web_link"],
                )
            )
        return ", ".join(packages), result

    def patches(self, bug: dict[str, Any]) -> list[Patch]:
        known = self.ubuntu_series()
        out = []
        for att in self._entries(bug["attachments_collection_link"]):
            title = att.get("title", "")
            if att.get("type") != "Patch" and not _PATCH_NAME.search(title):
                continue
            out.append(Patch(title, att["web_link"], att["data_link"], guess_series(title, known)))
        return out

    def comments(self, bug: dict[str, Any]) -> list[Comment]:
        return [
            Comment(
                author=_name(m.get("owner_link")),
                date=m.get("date_created", ""),
                body=m.get("content") or "",
                source="bug",
                url=m.get("web_link", ""),
            )
            for m in self._entries(bug["messages_collection_link"])
        ]

    def merge_proposals(self, bug: dict[str, Any]) -> list[MergeProposal]:
        link = bug.get("linked_merge_proposals_collection_link")
        if not link:
            return []
        known = self.ubuntu_series()
        proposals = []
        for mp in self._entries(link):
            source = mp.get("source_git_path") or _name(mp.get("source_branch_link"))
            target = mp.get("target_git_path") or _name(mp.get("target_branch_link"))
            diff_url = ""
            if mp.get("preview_diff_link"):
                try:
                    diff = self._get(mp["preview_diff_link"])
                    diff_url = f"{diff['web_link']}/+files/preview.diff"
                except LaunchpadError:
                    pass
            proposal = MergeProposal(
                web_url=mp["web_link"],
                status=mp.get("queue_status", ""),
                source=source,
                target=target,
                series=(guess_series(target, known) or [""])[0],
                diff_url=diff_url,
            )
            self._fill_review(mp, proposal)
            proposals.append(proposal)
        return proposals

    def _fill_review(self, mp: dict[str, Any], proposal: MergeProposal) -> None:
        try:
            for vote in self._entries(mp["votes_collection_link"]):
                if not vote.get("is_pending"):
                    comment = self._get(vote["comment_link"]) if vote.get("comment_link") else {}
                    proposal.votes.append(f"{_name(vote.get('reviewer_link'))}: {comment.get('vote') or 'Comment'}")
            for c in self._entries(mp["all_comments_collection_link"]):
                proposal.comments.append(
                    Comment(
                        author=_name(c.get("owner_link")),
                        date=c.get("date_created", ""),
                        body=c.get("content") or "",
                        source="mp",
                        url=c.get("web_link", ""),
                        vote=c.get("vote") or "",
                    )
                )
        except LaunchpadError:
            pass
