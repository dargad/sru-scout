"""Self-contained HTML report."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from jinja2 import Environment, PackageLoader, select_autoescape

from sru_scout.models import LEVELS, IssueReview

_env = Environment(loader=PackageLoader("sru_scout", "templates"), autoescape=select_autoescape(["html", "j2"]))


def render_html(reviews: list[IssueReview], title: str = "SRU review report") -> str:
    ordered = sorted(reviews, key=lambda r: (LEVELS.index(r.level), r.key))
    counts = Counter(r.level for r in reviews)
    return _env.get_template("report.html.j2").render(
        title=title,
        reviews=ordered,
        levels=LEVELS,
        counts={level: counts.get(level, 0) for level in LEVELS},
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
