from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import make_sample_deck as samples
from competition.ingest import load_bundle
from competition.schema import Alternative, Analysis, Claim, Section


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("samples")
    samples.build_sample_pptx(d / "Cairnly_Seed_Deck.pptx")
    samples.build_sample_pdf(d / "Cairnly_Seed_Deck.pdf")
    samples.build_support_pdf(d / "Cairnly_customer_interviews.pdf")
    samples.build_thin_pptx(d / "NovaCare_thin_deck.pptx")
    return d


@pytest.fixture
def bundle(sample_dir):
    b, errors = load_bundle(
        ("Cairnly_Seed_Deck.pptx", (sample_dir / "Cairnly_Seed_Deck.pptx").read_bytes()),
        [("Cairnly_customer_interviews.pdf", (sample_dir / "Cairnly_customer_interviews.pdf").read_bytes())],
    )
    assert not errors
    return b


def make_analysis(**overrides) -> Analysis:
    """A plausible model output grounded in the Cairnly sample materials."""
    data = dict(
        company_name="Cairnly",
        one_line="Cairnly sells supply forecasting and multi-distributor ordering to independent dental practices.",
        headline="Ordering is a distributor-rep habit, not a software category; Cairnly's wedge is the schedule-based forecast reps cannot offer.",
        status_quo=Section(body="In an independent practice, the office manager counts the supply closet into a spreadsheet, accepts the distributor rep's monthly suggested order, and fills gaps on Amazon Business at retail prices. Invoices are matched by hand at month end. Nobody links usage to the procedure schedule.", refs=["D3", "S1.1"]),
        failure_modes=Section(body="The workflow depends on one person with no time and a rep paid to sell. Practices report about three stockouts of a critical item per quarter and five hours a week of ordering work, and most buy from two or more distributors, so no single portal sees total spend.", refs=["D2", "S1.2"]),
        alternatives=[
            Alternative(name="Distributor portals", kind="incumbent", how_used="Free with the distributor account.", where_it_breaks="Locked to one distributor's catalog and prices.", refs=["D6"]),
            Alternative(name="Spreadsheets and the rep", kind="status quo", how_used="Office manager tracks counts by hand; rep suggests re-orders.", where_it_breaks="No usage forecast; stops when the one person is away.", refs=["D3", "S1.2"]),
            Alternative(name="Group purchasing orgs", kind="adjacent tool", how_used="Negotiate member discounts.", where_it_breaks="Price only; no ordering workflow.", refs=["D6"]),
        ],
        market_structure=Section(body="Spend runs through two national distributors whose portals keep practices inside one catalog. DSOs centralize purchasing and win on price, which leaves independents, roughly 70% of practices by the company's estimate, without a buyer of their own.", refs=["D5"]),
        wedge=Section(body="The entry point is the procedure schedule: Cairnly reads it from practice management software and forecasts usage, which a single-distributor portal has no reason to build. The pilot claims an 11% spend reduction, but on five internal practices; the wedge widens only if savings hold at scale.", refs=["D4", "D7"]),
        thesis_support="partially supported",
        thesis_support_rationale="Workflow and pain are well documented through interviews; savings and retention rest on five internal pilots.",
        evidence_gaps=["Independent measurement of the 11% savings", "Retention or churn of paying locations", "Response from distributors to multi-distributor ordering"],
        diligence_questions=["What happens to the rep relationship when Cairnly places the order?", "Which PMS integrations are live today?"],
        claims=[
            Claim(statement="Office managers spend about five hours a week on ordering.", refs=["D2"], quote="Office managers spend about 5 hours a week on ordering and reconciling invoices.", basis="stated in sources"),
            Claim(statement="Gaps are filled at retail prices.", refs=["D3"], quote="Gaps are filled from Amazon Business or a second distributor at retail prices", basis="stated in sources"),
            Claim(statement="17 of 22 practices use multiple distributors.", refs=["S1.2"], quote="17 of 22 practices buy from two or more distributors", basis="stated in sources"),
            Claim(statement="Pilot savings of 11%.", refs=["D7"], quote="Pilot practices cut supply spend 11% on average over 90 days", basis="stated in sources"),
            Claim(statement="Reps drive the default order.", refs=["D3"], quote="", basis="inference from sources"),
        ],
    )
    data.update(overrides)
    return Analysis(**data)


@pytest.fixture
def analysis() -> Analysis:
    return make_analysis()


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    """Stands in for anthropic.Anthropic; records the request it was given."""

    def __init__(self, parsed=None, stop_reason="end_turn"):
        self.calls: list[dict] = []
        message = SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, model="claude-opus-5-5",
                                  usage=SimpleNamespace(input_tokens=1000, output_tokens=2000))

        def stream(**kwargs):
            self.calls.append(kwargs)
            return FakeStream(message)

        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))
