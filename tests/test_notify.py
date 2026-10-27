import base64
import io
import json
import urllib.error
from html import escape

import pytest
from conftest import make_analysis

from competition import config, notify
from competition.evidence import ground, scan
from competition.notify import EmailOutcome, ResendError, ResendMailer, send_result_email
from competition.render import render_pdf
from competition.schema import Claim


@pytest.fixture
def run(bundle):
    a = make_analysis()
    g = ground(a, bundle)
    pdf = render_pdf(a, bundle.format_refs, grounding_note=g.note()).pdf
    return a, g, bundle, scan(bundle), pdf


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, body: bytes = b'{"message":"nope"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(notify.RESEND_ENDPOINT, code, "err", {}, io.BytesIO(body))


def test_default_recipient_is_info_address():
    assert config.NOTIFY_EMAIL_TO == "Info@tencapital.group"
    assert config.notify_recipients() == ["Info@tencapital.group"]


def test_message_summarises_the_result(run):
    a, g, bundle, report, _ = run
    subject, html, text = notify.build_message(a, g, bundle, report, model="claude-opus-5-5",
                                               input_tokens=4000, output_tokens=3000, scale=1.0,
                                               forced_shrink=False)
    assert subject == "[PARTIAL] Competition analysis: Cairnly"
    for body, enc in ((html, escape), (text, str)):
        assert enc(a.headline) in body
        assert "Cairnly_Seed_Deck.pptx" in body
        assert "4 of 4" in body
        assert enc(a.evidence_gaps[0]) in body
    assert "<script" not in html


def test_subject_flags_review_when_quotes_fail(bundle):
    a = make_analysis(claims=[Claim(statement="x", refs=["D2"], quote="never said this", basis="stated in sources")])
    g = ground(a, bundle)
    subject, _, text = notify.build_message(a, g, bundle, scan(bundle), model="m", input_tokens=1,
                                            output_tokens=1, scale=1.0, forced_shrink=False)
    assert "REVIEW" in subject
    assert "could not be found" in text


def test_attachments_are_pdf_draft_and_evidence(run):
    a, g, _, _, pdf = run
    files = notify.build_attachments(a, g, pdf)
    assert [f.filename for f in files] == ["Cairnly_Competition_Analysis.pdf", "Cairnly_competition_draft.json",
                                           "Cairnly_evidence.json"]
    assert base64.b64decode(files[0].to_payload()["content"]).startswith(b"%PDF")
    assert json.loads(files[2].content)["claims"][0]["check"] == "verified"


def test_mailer_posts_expected_request(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["req"] = request
        return FakeResponse(b'{"id":"msg_123"}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    mid = ResendMailer("re_test").send(sender="A <a@x.com>", to=["Info@tencapital.group"], subject="s",
                                       html="<p>h</p>", text="t", attachments=[notify.Attachment("f.pdf", b"%PDF")])
    req = seen["req"]
    body = json.loads(req.data)
    assert mid == "msg_123"
    assert req.full_url == notify.RESEND_ENDPOINT
    assert req.get_header("Authorization") == "Bearer re_test"
    assert req.get_header("User-agent") == notify.USER_AGENT
    assert body["to"] == ["Info@tencapital.group"]
    assert body["attachments"][0]["filename"] == "f.pdf"


def test_mailer_retries_server_errors_but_not_client_errors(monkeypatch):
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    calls = []

    def flaky(request, timeout):
        calls.append(1)
        if len(calls) < 2:
            raise _http_error(503)
        return FakeResponse(b'{"id":"ok"}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", flaky)
    assert ResendMailer("k").send(sender="a", to=["b"], subject="s", html="h", text="t") == "ok"
    assert len(calls) == 2

    calls.clear()

    def rejected(request, timeout):
        calls.append(1)
        raise _http_error(422, b'{"message":"domain not verified"}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", rejected)
    with pytest.raises(ResendError, match="422.*domain not verified"):
        ResendMailer("k").send(sender="a", to=["b"], subject="s", html="h", text="t")
    assert len(calls) == 1


def _send(run):
    a, g, bundle, report, pdf = run
    return send_result_email(a, g, bundle, report, pdf, model="m", input_tokens=1, output_tokens=1)


def test_never_sends_under_pytest(run, monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call under pytest"))
    assert _send(run).detail == "suppressed under pytest"


def test_disabled_without_key(run, monkeypatch):
    monkeypatch.setattr(notify, "_running_under_pytest", lambda: False)
    monkeypatch.setattr(config, "RESEND_API_KEY", None)
    outcome = _send(run)
    assert not outcome.sent and "RESEND_API_KEY" in outcome.detail


def test_sends_and_swallows_failures(run, monkeypatch):
    monkeypatch.setattr(notify, "_running_under_pytest", lambda: False)
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(config, "EMAIL_NOTIFICATIONS", True)
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b'{"id":"msg_9"}'))
    assert _send(run) == EmailOutcome(True, "msg_9", ["Info@tencapital.group"])

    def boom(req, timeout):
        raise _http_error(401)

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    outcome = _send(run)
    assert not outcome.sent and "401" in outcome.detail
