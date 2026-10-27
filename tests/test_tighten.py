from types import SimpleNamespace

import anthropic
from conftest import FakeStream, make_analysis

from competition import template as T
from competition.analyzer import TIGHTEN_ATTEMPTS, TIGHTEN_EFFORT, generate, tighten
from competition.evidence import scan
from competition.schema import Section

LONG = " ".join(f"word{i}" for i in range(150))


class EditorClient:
    """Fake model that answers a tighten request by returning `words` words per passage."""

    def __init__(self, words=40, main=None):
        # words: one count for every call, or a list giving the count per tighten call.
        self.words, self.main, self.calls = words, main, []

        def stream(**kw):
            self.calls.append(kw)
            fmt = kw["output_format"]
            if self.main is not None and fmt.__name__ == "Analysis":
                parsed = self.main
            else:
                n = self.words if isinstance(self.words, int) else self.words[
                    min(len([c for c in self.calls if c["output_format"].__name__ == "Rewrites"]),
                        len(self.words)) - 1]
                parsed = fmt(**{name: " ".join(["short"] * n) for name in fmt.model_fields})
            return FakeStream(SimpleNamespace(parsed_output=parsed, stop_reason="end_turn", model="m",
                                              usage=SimpleNamespace(input_tokens=10, output_tokens=5)))

        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))


def _over_draft():
    return make_analysis(status_quo=Section(body=LONG, refs=["D3"]), wedge=Section(body=LONG, refs=["D4"]))


def test_overruns_report_paths_and_limits():
    over = T.length_overruns(_over_draft())
    assert [(o.path, o.words, o.limit) for o in over] == [(("status_quo", "body"), 150, 85),
                                                          (("wedge", "body"), 150, 85)]


def test_tighten_rewrites_only_over_limit_passages():
    draft = _over_draft()
    untouched = draft.failure_modes.body
    client = EditorClient(words=60)
    result = tighten(draft, client)

    assert result.shortened == 2 and not result.remaining and result.error is None
    assert T.word_count(draft.status_quo.body) == 60 and T.word_count(draft.wedge.body) == 60
    assert draft.failure_modes.body == untouched
    assert draft.status_quo.refs == ["D3"]  # sources are never touched
    call = client.calls[0]
    assert set(call["output_format"].model_fields) == {"p0", "p1"}
    assert call["output_config"] == {"effort": TIGHTEN_EFFORT}
    prompt = call["messages"][0]["content"]
    assert 'hard_limit_words="85"' in prompt and 'target_words="72"' in prompt  # 85% margin


def test_no_call_when_everything_fits():
    client = EditorClient()
    assert tighten(make_analysis(), client).shortened == 0
    assert client.calls == []


def test_retries_and_never_lengthens():
    draft = _over_draft()
    client = EditorClient(words=200)  # a "rewrite" longer than the original must be rejected
    result = tighten(draft, client)
    assert len(client.calls) == TIGHTEN_ATTEMPTS
    assert result.shortened == 0 and len(result.remaining) == 2
    assert draft.status_quo.body == LONG


def test_api_failure_leaves_draft_unchanged():
    draft = _over_draft()

    def boom(**kw):
        raise anthropic.APIConnectionError(request=None)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=boom)))
    result = tighten(draft, client)
    assert result.error and "network" in result.error
    assert draft.wedge.body == LONG


def test_generate_tightens_model_output_and_counts_tokens(bundle):
    client = EditorClient(words=50, main=_over_draft())
    result = generate(bundle, "Cairnly", scan(bundle), client=client)
    assert len(client.calls) == 2
    assert T.length_issues(result.analysis) == []
    assert result.tighten.shortened == 2
    assert result.output_tokens == 5 + 5


def test_near_miss_is_retried_with_feedback_until_it_fits():
    """The reported case: the first rewrite lands at 89 words against an 85-word limit."""
    draft = make_analysis(wedge=Section(body=LONG, refs=["D4"]))
    client = EditorClient(words=[89, 58])
    result = tighten(draft, client)

    assert len(client.calls) == 2
    assert T.word_count(draft.wedge.body) == 58 and not result.remaining
    assert result.shortened == 1  # one passage, even though it was rewritten twice
    retry = client.calls[1]["messages"][0]["content"]
    assert 'previous_rewrite_words="89"' in retry and 'target_words="59"' in retry  # 70% on retry
