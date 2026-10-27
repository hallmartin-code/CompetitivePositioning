import pytest
from conftest import FakeClient, make_analysis

from competition import analyzer, config
from competition.analyzer import AnalysisError, generate
from competition.evidence import scan
from competition.ingest import SourceBundle, SourceDocument, SourcePage
from competition.prompts import build_user_message


def test_request_shape_and_result(bundle):
    client = FakeClient(parsed=make_analysis(company_name="Read off the deck"))
    result = generate(bundle, "Cairnly", scan(bundle), client=client)

    call = client.calls[0]
    assert call["model"] == config.MODEL
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_format"].__name__ == "Analysis"
    assert call["fallbacks"] == "default" and analyzer.FALLBACK_BETA in call["betas"]
    assert "temperature" not in call
    # the user-confirmed name wins
    assert result.analysis.company_name == "Cairnly"
    assert result.grounding.verified == 4


def test_prompt_tags_every_page_and_reports_unreadable_ones(bundle):
    text = build_user_message(bundle, "Cairnly", scan(bundle))
    assert '<page ref="D3" location="slide 3">' in text
    assert '<page ref="S1.2" location="page 2">' in text
    assert "[no extractable text: likely an image]" in text
    assert "Extraction notes" in text
    assert "[Speaker notes]" in text


@pytest.mark.parametrize("stop_reason, match", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_bad_stop_reasons_raise_actionable_errors(bundle, stop_reason, match):
    with pytest.raises(AnalysisError, match=match):
        generate(bundle, "Cairnly", scan(bundle), client=FakeClient(parsed=make_analysis(), stop_reason=stop_reason))


def test_refuses_to_call_model_with_no_readable_text():
    empty = SourceBundle([SourceDocument("D", "scan.pdf", "pdf", [SourcePage("D1", 1, "")])])
    with pytest.raises(AnalysisError, match="no extractable text|nothing"):
        generate(empty, "X", scan(empty), client=FakeClient())


def test_oversized_input_is_reported_not_truncated(monkeypatch, bundle):
    monkeypatch.setattr(config, "MAX_SOURCE_CHARS", 100)
    client = FakeClient(parsed=make_analysis())
    with pytest.raises(AnalysisError, match="nothing is truncated"):
        generate(bundle, "Cairnly", scan(bundle), client=client)
    assert not client.calls
