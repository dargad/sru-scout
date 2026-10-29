from sru_scout.jira import JiraClient


class FakeResponse:
    ok = True
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, pages):
        self.pages, self.headers, self.auth, self.calls = pages, {}, None, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.endswith("/remotelink"):
            return FakeResponse([{"object": {"url": "https://bugs.launchpad.net/bugs/555", "title": "t"}}])
        return FakeResponse(self.pages[params["startAt"]])


def raw(key, summary):
    return {"key": key, "fields": {"summary": summary, "status": {"name": "In Review"}, "assignee": {"displayName": "Ann"}}}


def test_board_pagination_and_remote_link_fallback():
    pages = {
        0: {"total": 3, "issues": [raw("SI-1", "foo (LP: #11)"), raw("SI-2", "no bug in summary")]},
        2: {"total": 3, "issues": [raw("SI-3", "bar LP: #33")]},
    }
    session = FakeSession(pages)
    client = JiraClient("a@b.c", "tok", session=session)
    issues = list(client.board_issues(1017, "statusCategory != Done"))
    assert [i.key for i in issues] == ["SI-1", "SI-2", "SI-3"]
    assert [i.bug_ids() for i in issues] == [[11], [555], [33]]
    assert issues[0].assignee == "Ann" and issues[0].url.endswith("/browse/SI-1")
    assert session.auth == ("a@b.c", "tok")
    assert "/rest/agile/1.0/board/1017/issue" in session.calls[0][0]


def test_limit():
    session = FakeSession({0: {"total": 2, "issues": [raw("SI-1", "LP: #1"), raw("SI-2", "LP: #2")]}})
    client = JiraClient("a", "b", session=session)
    assert len(list(client.board_issues(limit=1))) == 1
