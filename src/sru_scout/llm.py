"""Optional LLM pass over review comments (any OpenAI-compatible chat completions endpoint)."""

from __future__ import annotations

import json
import os
import re

import requests

from sru_scout.http import make_session
from sru_scout.models import Blocker, BugReview, Comment

MAX_CHARS = 12000

PROMPT = """You assist an Ubuntu SRU (Stable Release Update) reviewer.
Below are the comments on a Launchpad bug and its merge proposals, oldest first.
List only issues that still block, or may block, releasing the fix to the stable series.
Ignore issues that later comments show were resolved. Reply with a JSON array only; each item:
{"severity": "blocker" | "concern", "summary": "<one sentence>", "comment_url": "<url or empty>"}
Reply [] if nothing blocks the release."""


class LLMAnalyzer:
    def __init__(self, url: str, model: str, api_key: str = "", session: requests.Session | None = None) -> None:
        self.url = url
        self.model = model
        self.session = session or make_session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    @classmethod
    def from_env(cls) -> LLMAnalyzer | None:
        url = os.environ.get("SRU_SCOUT_LLM_URL")
        model = os.environ.get("SRU_SCOUT_LLM_MODEL")
        if not url or not model:
            return None
        return cls(url, model, os.environ.get("SRU_SCOUT_LLM_API_KEY", ""))

    def analyze(self, bug: BugReview, comments: list[Comment]) -> list[Blocker]:
        transcript = []
        for c in comments + [mc for mp in bug.merge_proposals for mc in mp.comments]:
            vote = f" [vote: {c.vote}]" if c.vote else ""
            transcript.append(f"--- {c.author} {c.date}{vote} {c.url}\n{c.body.strip()}")
        text = "\n".join(transcript)[-MAX_CHARS:]
        resp = self.session.post(
            self.url,
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": f"Bug: {bug.title}\nTags: {', '.join(bug.tags)}\n\n{text}"},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        return parse_reply(resp.json()["choices"][0]["message"]["content"], bug.url)


def parse_reply(content: str, default_url: str) -> list[Blocker]:
    match = re.search(r"\[.*\]", content, re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    result = []
    for item in items:
        if not isinstance(item, dict) or not item.get("summary"):
            continue
        severity = "blocker" if item.get("severity") == "blocker" else "concern"
        result.append(Blocker(severity, str(item["summary"]), "llm", item.get("comment_url") or default_url))
    return result
