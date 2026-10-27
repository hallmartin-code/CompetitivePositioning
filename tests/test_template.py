import typing

import pymupdf
from conftest import make_analysis

from competition import template as T
from competition.schema import Alternative, Analysis, Claim, Section

# Words that only appear in the sample materials; none may leak into the template.
SAMPLE_TERMS = ("cairnly", "novacare", "dental", "distributor", "amazon", "dso", "ohio", "$249", "22 practices")


def test_template_is_company_neutral():
    text = T.TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    assert not [t for t in SAMPLE_TERMS if t in text]


def test_every_analysis_field_is_defined_by_the_template():
    for name in Analysis.model_fields:
        if name not in ("company_name", "claims"):
            assert T.field(name)
    for name in ("name", "kind", "how_used", "where_it_breaks", "refs"):
        assert T.field(name)
    for name in Claim.model_fields:
        assert T.claim_field(name) is not None


def test_enum_options_match_the_schema():
    assert T.field("thesis_support")["options"] == list(typing.get_args(Analysis.model_fields["thesis_support"].annotation))
    assert T.field("kind")["options"] == list(typing.get_args(Alternative.model_fields["kind"].annotation))
    basis = next(f for f in T.load()["evidence_record"]["fields"] if f["key"] == "basis")
    assert basis["options"] == list(typing.get_args(Claim.model_fields["basis"].annotation))


def test_schema_descriptions_and_section_order_come_from_template():
    assert "55-85 words" in Analysis.model_fields["status_quo"].description
    assert [title for _, title, _ in make_analysis().sections()] == [s["title"] for s in T.sections()]


def test_length_issues_flags_overruns_only():
    assert T.length_issues(make_analysis()) == []
    long = make_analysis(wedge=Section(body="word " * 120, refs=["D4"]))
    assert any("Where the wedge emerges: 120 words (limit 85)" in i for i in T.length_issues(long))


def test_blank_template_renders_one_placeholder_page(tmp_path):
    out = T.render_blank(tmp_path / "blank.pdf")
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1
        text = " ".join(doc[0].get_text().split())
    assert "[Company Name]" in text and "[DATE]" in text
    for sec in T.sections():
        assert sec["title"] in text
    assert not [t for t in SAMPLE_TERMS if t in text.lower()]
