from conftest import make_analysis

from competition.evidence import ground, scan
from competition.ingest import load_bundle
from competition.schema import Claim


def test_scan_finds_evidence_in_sample_deck(bundle):
    report = scan(bundle)
    status = {r.category.key: r.status for r in report.results}
    assert status["workflow"] == "found"
    assert status["competitors"] == "found"   # dedicated Competition slide counts
    assert status["traction"] == "found"
    assert "D3" in next(r for r in report.results if r.category.key == "workflow").refs
    assert report.extraction_warnings  # the image-only slide
    assert report.readable_pages == report.total_pages - 1


def test_scan_exposes_thin_deck(sample_dir):
    thin, _ = load_bundle(("NovaCare_thin_deck.pptx", (sample_dir / "NovaCare_thin_deck.pptx").read_bytes()), [])
    report = scan(thin)
    missing = {r.category.key for r in report.missing}
    assert {"workflow", "competitors", "traction", "pricing"} <= missing


def test_grounding_verifies_quotes_and_keeps_partial_rating(bundle, analysis):
    report = ground(analysis, bundle)
    assert report.verified == 4 and report.factual == 4
    assert report.unverified == 0
    assert analysis.thesis_support == "partially supported"
    assert "4 of 4" in report.note()


def test_grounding_strips_unknown_refs_and_flags_fabricated_quote(bundle):
    a = make_analysis(claims=[
        Claim(statement="Made up", refs=["D2", "D99"], quote="Cairnly has 400 paying customers nationwide",
              basis="stated in sources"),
    ])
    a.wedge.refs = ["Z1"]
    report = ground(a, bundle)
    assert a.claims[0].refs == ["D2"]
    assert a.wedge.refs == []
    assert report.unverified == 1
    assert any("D99" in i for i in report.issues)
    assert any("Where the wedge emerges" in i and "analyst opinion" in i for i in report.issues)


def test_grounding_corrects_misattributed_quote(bundle):
    a = make_analysis(claims=[
        Claim(statement="Multi-distributor buying", refs=["D2"],
              quote="17 of 22 practices buy from two or more distributors", basis="stated in sources"),
    ])
    report = ground(a, bundle)
    assert report.claim_checks[0].status == "found elsewhere"
    assert a.claims[0].refs == ["S1.2"]


def test_supported_rating_downgraded_when_claims_do_not_verify(bundle):
    a = make_analysis(thesis_support="supported", claims=[
        Claim(statement="x", refs=["D2"], quote="a sentence that is not in the deck at all", basis="stated in sources"),
        Claim(statement="y", refs=["D3"], quote="another invented sentence about the market", basis="stated in sources"),
        Claim(statement="z", refs=["D7"], quote="5 paying customers at $249 per location per month", basis="stated in sources"),
    ])
    report = ground(a, bundle)
    assert a.thesis_support == "partially supported"
    assert report.downgraded_from == "supported"


def test_quote_matching_tolerates_typography_and_ellipsis(bundle):
    a = make_analysis(claims=[
        Claim(statement="q", refs=["S1.1"], quote="“I order whatever the rep puts in front of me … I don’t have time”",
              basis="stated in sources"),
    ])
    assert ground(a, bundle).claim_checks[0].status == "verified"
