"""Runs `sru-lint check <url>` and parses its JSON output."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading

from sru_scout.models import LintFinding, LintResult

_SEVERITIES = {"error", "warning", "info"}


def parse_findings(stdout: str) -> list[LintFinding]:
    start = stdout.find("[")
    if start < 0:
        raise ValueError("no JSON array in sru-lint output")
    data = json.loads(stdout[start : stdout.rfind("]") + 1])
    findings = []
    for item in data:
        span = item.get("span") or {}
        severity = str(item.get("severity", "info")).lower()
        findings.append(
            LintFinding(
                rule_id=item.get("rule_id", ""),
                severity=severity if severity in _SEVERITIES else "info",
                message=item.get("message", ""),
                path=span.get("path", ""),
                line=span.get("start_line", 0) or 0,
                doc_url=item.get("doc_url", ""),
            )
        )
    return findings


class SruLint:
    """Thread-safe runner that caches results per URL."""

    def __init__(self, binary: str = "sru-lint", timeout: int = 180) -> None:
        self.binary = binary
        self.timeout = timeout
        self._cache: dict[str, LintResult] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def check(self, url: str) -> LintResult:
        with self._lock:
            cached = self._cache.get(url)
        if cached:
            return cached
        result = self._run(url)
        with self._lock:
            self._cache[url] = result
        return result

    def _run(self, url: str) -> LintResult:
        try:
            proc = subprocess.run(
                [self.binary, "check", "--format", "json", url],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return LintResult(url, ran=False, error=f"'{self.binary}' not found in PATH")
        except subprocess.TimeoutExpired:
            return LintResult(url, ran=False, error=f"sru-lint timed out after {self.timeout}s")
        try:
            return LintResult(url, findings=parse_findings(proc.stdout))
        except ValueError as exc:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            tail = detail[-1] if detail else f"exit code {proc.returncode}"
            return LintResult(url, ran=False, error=f"could not parse sru-lint output ({exc}): {tail}")
