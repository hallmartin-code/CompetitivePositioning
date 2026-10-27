from datetime import date

import pymupdf
from conftest import make_analysis

from competition.render import render_pdf, render_preview_png
from competition.schema import Section


def _text(pdf: bytes) -> tuple[int, str]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count, "\n".join(p.get_text() for p in doc)


def test_one_page_memo_with_sources_and_footer(bundle, analysis):
    result = render_pdf(analysis, bundle.format_refs, compiled_on=date(2026, 10, 27),
                        grounding_note="4 of 4 quoted claims were verified against the source text.")
    pages, text = _text(result.pdf)
    assert pages == 1
    assert not result.forced_shrink
    flat = " ".join(text.split())
    assert "Cairnly Competition Analysis" in flat
    assert "Compiled on October 27, 2026 by TEN Capital Network" in flat
    assert "Source: Deck s.3; Cairnly customer interviews p.1" in flat
    assert "How customers solve it today" in flat and "Where the wedge emerges" in flat
    assert "Evidence for the thesis: Partially supported." in flat
    assert "4 of 4 quoted claims" in flat
    assert "not a registered broker-dealer" in flat


def test_overlong_edits_still_fit_on_one_page(bundle):
    long = " ".join(["Office managers reconcile invoices by hand every month."] * 40)
    a = make_analysis(status_quo=Section(body=long, refs=["D3"]), wedge=Section(body=long, refs=["D4"]))
    result = render_pdf(a, bundle.format_refs)
    assert _text(result.pdf)[0] == 1
    assert result.scale < 1.0 or result.forced_shrink


def test_unsourced_section_is_labelled(analysis):
    analysis.market_structure.refs = []
    _, text = _text(render_pdf(analysis).pdf)
    assert "none in the materials; analyst view" in " ".join(text.split())


def test_preview_png(analysis):
    png = render_preview_png(render_pdf(analysis).pdf, zoom=0.5)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
