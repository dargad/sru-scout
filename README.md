# sru-scout

Agent that reviews the Ubuntu SRU Jira board ([SI board 1017](https://warthogs.atlassian.net/jira/software/c/projects/SI/boards/1017)) and tells the reviewer where human attention is needed.

For every card it:

1. Finds the Launchpad bug(s) referenced by the card (summary, description, links, remote links, comments as a last resort).
2. Reads the per-series bug task status. Series that are *Fix Committed* / *Fix Released* (or closed / EOL) are skipped.
3. For the remaining series it collects attached patches and linked merge proposals and runs `sru-lint check <url>` on each (patch attachment data, or the merge proposal preview diff).
4. Scans bug tags, bug comments, merge proposal comments, votes and status for blockers (rule based; optionally an LLM pass).
5. Classifies each card as `blocked`, `engineer-attention`, `sponsor-attention`, `review`, `ready` or `done` and renders a CLI and/or HTML report.

## Setup

```sh
snap install --edge sru-lint        # required unless --no-lint
uv sync
export JIRA_EMAIL=you@canonical.com
export JIRA_API_TOKEN=...           # https://id.atlassian.com/manage-profile/security/api-tokens
```

Jira Cloud rejects anonymous queries on this site, so credentials are required for board access.
Launchpad is accessed anonymously: private bugs are reported as "not accessible".

## Usage

```sh
# whole board (cards not in a Done status category), CLI summary + HTML + JSON
uv run sru-scout review --html reports/sru.html --json reports/sru.json

# specific cards
uv run sru-scout review --issue SI-1234 --issue SI-1250 -v

# Launchpad bugs directly, no Jira credentials needed
uv run sru-scout review --bug 2172396 --html /tmp/x.html

# narrow the board with JQL
uv run sru-scout review --jql 'status = "In Review" ORDER BY updated DESC' --limit 20
```

Use `--fail-on-attention` to get exit code 1 when any card is blocked or needs engineer or sponsor attention (cron/CI).

### Levels

| Level | Meaning |
|-------|---------|
| `blocked` | Unresolved strong blocker: verification failed, `block-proposed*` / `regression-*` tags, rejected/disapproved MP, NACK, FTBFS, autopkgtest regressions |
| `engineer-attention` | The patch author / bug owner has to act: incomplete SRU template, no patch/MP for a pending series, sru-lint errors, reviewer change requests, bug Incomplete, not verified in time or not building in -proposed, inaccessible bug, no bug found in the card |
| `sponsor-attention` | A sponsor / uploader has to act: nothing is left for the engineer and the patches/MPs for series not yet in -proposed wait for review and upload, or sru-lint could not run on them |
| `review` | sru-lint warnings, possibly-resolved concerns, development release not fixed yet (listed under `sponsor-attention` when a sponsor is needed anyway) |
| `ready` | Nothing to do right now, e.g. the fix is in -proposed awaiting verification |
| `done` | Every series task is Fix Committed/Released or closed |

Comment analysis is keyword based and deliberately conservative: it flags things for a human to read, it does not approve anything. A finding is marked "possibly resolved" when a later comment contains words like "fixed" or "updated".

### Optional LLM comment analysis

`--llm` sends the comment transcript to any OpenAI-compatible chat completions endpoint:

```sh
export SRU_SCOUT_LLM_URL=https://api.openai.com/v1/chat/completions
export SRU_SCOUT_LLM_MODEL=gpt-4o-mini
export SRU_SCOUT_LLM_API_KEY=...
```

The LLM output is added to the rule-based findings; failures are logged and ignored.

## Limitations

- Patches are attributed to series from their title and merge proposals from the target branch; if a patch cannot be attributed it is assumed to cover every pending series.
- The Jira board listing uses the agile API with the board's own filter; it could not be exercised against the live site without credentials (the tests use fixtures).

## Development

```sh
uv run pytest
```
