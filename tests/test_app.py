"""Drive the Streamlit app headlessly. AppTest cannot upload files, so uploads are
seeded into session state exactly as the uploader callback stores them."""

import hashlib
from pathlib import Path

import pymupdf
import pytest
from conftest import make_analysis
from streamlit.testing.v1 import AppTest

from competition import analyzer
from competition.analyzer import AnalysisResult
from competition.evidence import ground

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _seed(at: AppTest, path: Path) -> None:
    data = path.read_bytes()
    at.session_state["primary"] = (path.name, data, hashlib.sha1(data).hexdigest())


@pytest.fixture
def fake_generate(monkeypatch):
    calls = []

    def fake(bundle, company_name, report, client=None):
        calls.append(company_name)
        a = make_analysis(company_name=company_name)
        return AnalysisResult(a, ground(a, bundle), "claude-opus-5-5", 1, 1)

    monkeypatch.setattr(analyzer, "generate", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return calls


def test_readiness_shows_warnings_and_missing_evidence_for_thin_deck(sample_dir, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=60)
    _seed(at, sample_dir / "NovaCare_thin_deck.pptx")
    at.run()
    assert not at.exception
    assert at.text_input(key="company_name").value == "NovaCare AI"
    assert any("say little about" in w.value for w in at.warning)
    assert any("API key" in i.value for i in at.info)
    assert next(b for b in at.button if b.label == "Generate analysis").disabled  # blocked without a key


def test_generate_edit_and_export_without_rerunning_model(sample_dir, fake_generate):
    at = AppTest.from_file(APP, default_timeout=60)
    _seed(at, sample_dir / "Cairnly_Seed_Deck.pptx")
    at.run()
    assert any("slide 8" in w.value for w in at.warning)  # extraction warning before generation

    next(b for b in at.button if b.label == "Generate analysis").click().run()
    assert not at.exception
    assert fake_generate == ["Cairnly"]

    headline = at.text_area(key="1_headline")
    headline.set_value("Edited headline about distributor reps.").run()
    at.run()  # extra reruns must neither call the model nor lose the edit
    assert fake_generate == ["Cairnly"]
    assert at.session_state["draft"].headline == "Edited headline about distributor reps."

    pdf = next(d for d in at.get("download_button") if "PDF" in d.proto.label)
    assert pdf is not None
    rendered = at.session_state["pdf_cache"][1].pdf
    with pymupdf.open(stream=rendered, filetype="pdf") as doc:
        assert doc.page_count == 1
        assert "Edited headline about distributor reps." in " ".join(doc[0].get_text().split())
    assert any("Regenerate" in b.label for b in at.button)


def test_each_generation_is_emailed_once(sample_dir, fake_generate, monkeypatch):
    from competition import config, notify
    from competition.notify import EmailOutcome

    sent = []
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(config, "EMAIL_NOTIFICATIONS", True)
    monkeypatch.setattr(notify, "send_result_email",
                        lambda a, g, b, r, pdf, **kw: sent.append(pdf) or EmailOutcome(True, "id", ["Info@tencapital.group"]))

    at = AppTest.from_file(APP, default_timeout=60)
    _seed(at, sample_dir / "Cairnly_Seed_Deck.pptx")
    at.run()
    assert any("emailed to <code>Info@tencapital.group</code>" in m.value for m in at.markdown)

    next(b for b in at.button if b.label == "Generate analysis").click().run()
    assert len(sent) == 1 and sent[0].startswith(b"%PDF")
    assert any("Result emailed to Info@tencapital.group" in s.value for s in at.success)

    at.text_area(key="1_headline").set_value("An edit").run()
    assert len(sent) == 1  # edits and reruns do not send again

    next(b for b in at.button if b.label == "Regenerate analysis").click().run()
    assert len(sent) == 2


def test_shorten_to_fit_button_fixes_overlong_draft(sample_dir, monkeypatch):
    from test_tighten import EditorClient, _over_draft

    from competition import template as T

    def fake(bundle, company_name, report, client=None):
        a = _over_draft()
        return AnalysisResult(a, ground(a, bundle), "claude-opus-5-5", 1, 1)

    monkeypatch.setattr(analyzer, "generate", fake)
    monkeypatch.setattr(analyzer, "make_client", lambda key=None: EditorClient(words=50))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    at = AppTest.from_file(APP, default_timeout=60)
    _seed(at, sample_dir / "Cairnly_Seed_Deck.pptx")
    at.run()
    next(b for b in at.button if b.label == "Generate analysis").click().run()
    assert any("Over the template's length limits" in w.value for w in at.warning)

    next(b for b in at.button if b.label == "Shorten to fit").click().run()
    assert not at.exception
    assert T.length_issues(at.session_state["draft"]) == []
    assert at.text_area(key="2_status_quo_body").value == " ".join(["short"] * 50)
    assert not any("Over the template's length limits" in w.value for w in at.warning)
    assert any("now fits the template" in s.value for s in at.success)
