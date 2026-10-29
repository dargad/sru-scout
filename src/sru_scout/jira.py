"""Minimal Jira Cloud client: lists board issues and extracts Launchpad bug references."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

from sru_scout.http import TIMEOUT, make_session

DEFAULT_BASE_URL = "https://warthogs.atlassian.net"
DEFAULT_BOARD = 1017
DEFAULT_JQL = "statusCategory != Done"

_BUG_PATTERNS = (
    re.compile(r"launchpad\.net/\S*?/\+?bugs?/(\d+)", re.I),
    re.compile(r"launchpad\.net/\+?bugs?/(\d+)", re.I),
    re.compile(r"\bLP\s*:?\s*#\s*(\d+)", re.I),
    re.compile(r"\bLP\s+bug\s*#?\s*(\d+)", re.I),
)


class JiraError(RuntimeError):
    pass


@dataclass
class JiraIssue:
    key: str
    url: str
    summary: str
    status: str = ""
    assignee: str = ""
    # Texts in priority order: summary, description, remote links, issue links, comments.
    texts: dict[str, str] = field(default_factory=dict)

    def bug_ids(self) -> list[int]:
        """Prefer explicit places; only fall back to comments, which often mention other bugs."""
        for source in ("summary", "description", "links"):
            ids = extract_bug_ids(self.texts.get(source, ""))
            if ids:
                return ids
        return extract_bug_ids(self.texts.get("comments", ""))


def extract_bug_ids(text: str) -> list[int]:
    found: list[tuple[int, int]] = []
    for pattern in _BUG_PATTERNS:
        found.extend((m.start(), int(m.group(1))) for m in pattern.finditer(text))
    seen: list[int] = []
    for _, bug in sorted(found):
        if bug not in seen:
            seen.append(bug)
    return seen


def adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format (or a plain string) to text, keeping link targets."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    parts: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if not isinstance(n, dict):
            return
        if n.get("type") == "text":
            parts.append(n.get("text", ""))
        attrs = n.get("attrs") or {}
        if attrs.get("url"):
            parts.append(f" {attrs['url']} ")
        for mark in n.get("marks") or []:
            href = (mark.get("attrs") or {}).get("href")
            if href:
                parts.append(f" {href} ")
        if n.get("type") in ("paragraph", "heading", "listItem", "hardBreak"):
            parts.append("\n")
        walk(n.get("content"))

    walk(node)
    return "".join(parts)


class JiraClient:
    def __init__(
        self,
        email: str,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or make_session()
        self.session.auth = (email, token)
        self.session.headers["Accept"] = "application/json"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=TIMEOUT)
        if resp.status_code in (401, 403):
            raise JiraError(
                f"Jira rejected the credentials ({resp.status_code}); "
                "check JIRA_EMAIL / JIRA_API_TOKEN and board permissions"
            )
        if not resp.ok:
            raise JiraError(f"Jira request {path} failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def board_issues(
        self, board: int = DEFAULT_BOARD, jql: str = DEFAULT_JQL, limit: int | None = None
    ) -> Iterator[JiraIssue]:
        """Yield issues of a board; the board's own filter is applied server side, `jql` narrows it."""
        start = 0
        yielded = 0
        while True:
            page = self._get(
                f"/rest/agile/1.0/board/{board}/issue",
                {
                    "startAt": start,
                    "maxResults": 50,
                    "jql": jql,
                    "fields": "summary,status,assignee,description,comment,issuelinks,labels",
                },
            )
            issues = page.get("issues", [])
            for raw in issues:
                yield self._parse(raw)
                yielded += 1
                if limit and yielded >= limit:
                    return
            start += len(issues)
            if not issues or start >= page.get("total", 0):
                return

    def issue(self, key: str) -> JiraIssue:
        raw = self._get(
            f"/rest/api/3/issue/{key}",
            {"fields": "summary,status,assignee,description,comment,issuelinks,labels"},
        )
        return self._parse(raw)

    def remote_links(self, key: str) -> str:
        try:
            links = self._get(f"/rest/api/3/issue/{key}/remotelink")
        except JiraError:
            return ""
        return "\n".join(
            f"{(link.get('object') or {}).get('url', '')} {(link.get('object') or {}).get('title', '')}"
            for link in links
        )

    def _parse(self, raw: dict[str, Any]) -> JiraIssue:
        f = raw.get("fields", {})
        key = raw["key"]
        comments = (f.get("comment") or {}).get("comments", [])
        link_text = []
        for link in f.get("issuelinks") or []:
            other = link.get("outwardIssue") or link.get("inwardIssue") or {}
            link_text.append((other.get("fields") or {}).get("summary", ""))
        issue = JiraIssue(
            key=key,
            url=f"{self.base_url}/browse/{key}",
            summary=f.get("summary") or "",
            status=(f.get("status") or {}).get("name", ""),
            assignee=(f.get("assignee") or {}).get("displayName", ""),
            texts={
                "summary": f.get("summary") or "",
                "description": adf_to_text(f.get("description")),
                "links": "\n".join(link_text),
                "comments": "\n".join(adf_to_text(c.get("body")) for c in comments),
            },
        )
        if not any(extract_bug_ids(issue.texts[k]) for k in ("summary", "description", "links")):
            issue.texts["links"] += "\n" + self.remote_links(key)
        return issue
