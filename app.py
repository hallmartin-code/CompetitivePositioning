"""Streamlit UI: pitch deck in, one-page competition analysis PDF out.

    streamlit run app.py        (or simply: python app.py)

Everything the user uploads, generates and edits lives in st.session_state, so
revising the memo never requires re-uploading files or re-running the model.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _relaunch_under_streamlit() -> None:
    """Hand a plain ``python app.py`` (e.g. VS Code's Run button) to ``streamlit run``.

    Outside ``streamlit run`` the script executes once in "bare mode": session
    state does not persist and st.stop() is a no-op, so the app falls through
    and crashes. Streamlit is always imported before ``streamlit run`` executes
    the script, so its absence identifies a plain launch using only the stdlib,
    before any third-party import can fail on an interpreter that lacks them.
    The project venv is preferred, and the cwd is the project root so
    .streamlit/config.toml (theme, static favicon) is found.
    """
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = str(venv) if venv.exists() else sys.executable
    args = sys.argv[1:]
    # Hosts such as Railway route traffic to $PORT on all interfaces; Streamlit's
    # default of localhost:8501 would leave the container unreachable.
    port = os.environ.get("PORT")
    if port and not any(a.startswith("--server.port") for a in args):
        args = ["--server.port", port, "--server.address", "0.0.0.0", "--server.headless", "true", *args]
    cmd = [python, "-m", "streamlit", "run", str(ROOT / "app.py"), *args]
    os.chdir(ROOT)
    if os.name != "nt":
        # Replace this process so the platform's SIGTERM reaches Streamlit directly.
        os.execv(python, cmd)
    try:
        sys.exit(subprocess.call(cmd))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__" and "streamlit" not in sys.modules:
    _relaunch_under_streamlit()

import pandas as pd
import streamlit as st

sys.path.insert(0, str(ROOT / "src"))

from competition import config  # noqa: E402
from competition.analyzer import AnalysisError, generate, make_client  # noqa: E402
from competition.evidence import EvidenceReport, scan  # noqa: E402
from competition.ingest import IngestError, SourceBundle, guess_company_name, load_bundle  # noqa: E402
from competition.notify import send_result_email  # noqa: E402
from competition.render import render_pdf, render_preview_png  # noqa: E402
from competition.schema import Alternative, Analysis  # noqa: E402
from competition.template import length_issues  # noqa: E402
from competition import ui  # noqa: E402

st.set_page_config(page_title="TEN Capital · Competition Analyzer",
                   page_icon=str(config.FAVICON_PATH), layout="wide")
ui.apply_theme()

ss = st.session_state
ss.setdefault("primary", None)        # (name, bytes, sha)
ss.setdefault("supporting", [])       # [(name, bytes, sha)]
ss.setdefault("bundle_key", None)
ss.setdefault("bundle", None)
ss.setdefault("load_errors", [])
ss.setdefault("result", None)         # AnalysisResult from the model
ss.setdefault("draft", None)          # Analysis being edited
ss.setdefault("draft_version", 0)     # bumps on (re)generation so edit widgets reset
ss.setdefault("pdf_cache", (None, None))
ss.setdefault("user_api_key", "")
ss.setdefault("email_status", None)  # EmailOutcome of the last generation

STATUS_ICON = {"found": "✅", "thin": "🟡", "missing": "❌"}
KINDS = list(Alternative.model_fields["kind"].annotation.__args__)
SUPPORT = ["supported", "partially supported", "not supported"]


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _pdf_key(analysis: Analysis, note: str) -> str:
    return hashlib.sha1((analysis.model_dump_json() + note).encode()).hexdigest()


def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or ss.user_api_key or None


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Settings")
    if os.environ.get("ANTHROPIC_API_KEY"):
        st.success("Anthropic API key loaded from the environment.")
    else:
        ss.user_api_key = st.text_input("Anthropic API key", value=ss.user_api_key, type="password",
                                        help="Kept only in this browser session. Or set ANTHROPIC_API_KEY in .env.")
    st.caption(f"Model: `{config.MODEL}` · effort `{config.EFFORT}`")
    st.caption("Result emails: " + (", ".join(config.notify_recipients()) if config.notifications_enabled()
                                    else "off (set RESEND_API_KEY in .env)"))
    st.divider()
    if st.button("Start over", help="Clear uploads, analysis and edits from this session."):
        for key in list(ss.keys()):
            del ss[key]
        st.rerun()



def _stop() -> None:
    ui.footer()
    st.stop()


ui.brand()
# The uploader's state is known before it renders, so the step chips are current on this run.
has_deck = ss.primary is not None or ss.get("up_primary") is not None
ui.hero(3 if ss.draft is not None else 1 if has_deck else 0)

# ---------------------------------------------------------------------------
# 1. Materials
# ---------------------------------------------------------------------------

ui.section(1, "Materials")
c1, c2 = st.columns(2)
with c1:
    up = st.file_uploader("Pitch deck (PDF, PPTX or PPT)", type=["pdf", "pptx", "ppt"], key="up_primary")
    if up is not None:
        data = up.getvalue()
        if ss.primary is None or ss.primary[2] != _sha(data):
            ss.primary = (up.name, data, _sha(data))
with c2:
    ups = st.file_uploader("Supporting materials (optional)", type=["pdf", "pptx", "ppt"],
                           accept_multiple_files=True, key="up_supporting",
                           help="Customer interviews, data room excerpts, market notes.")
    known = {s[2] for s in ss.supporting}
    for f in ups or []:
        data = f.getvalue()
        if _sha(data) not in known:
            ss.supporting.append((f.name, data, _sha(data)))
            known.add(_sha(data))

if ss.primary or ss.supporting:
    st.markdown("**Kept for this session**")
    if ss.primary:
        a, b = st.columns([6, 1], vertical_alignment="center")
        a.markdown(f"Deck: `{ss.primary[0]}`")
        if b.button("Remove", key="rm_primary"):
            ss.primary = None
            st.rerun()
    for i, (name, _, sha) in enumerate(list(ss.supporting)):
        a, b = st.columns([6, 1], vertical_alignment="center")
        a.markdown(f"Supporting {i + 1}: `{name}`")
        if b.button("Remove", key=f"rm_{sha}"):
            ss.supporting = [s for s in ss.supporting if s[2] != sha]
            st.rerun()

if ss.primary is None:
    st.info("Upload a pitch deck to begin.")
    _stop()

bundle_key = (ss.primary[2], tuple(s[2] for s in ss.supporting))
if ss.bundle_key != bundle_key:
    try:
        with st.spinner("Reading materials…"):
            bundle, errors = load_bundle(ss.primary[:2], [s[:2] for s in ss.supporting])
    except IngestError as exc:
        ss.bundle, ss.bundle_key = None, None
        st.error(str(exc))
        _stop()
    ss.bundle, ss.load_errors, ss.bundle_key = bundle, errors, bundle_key
    if not ss.get("company_name"):
        ss.company_name = guess_company_name(bundle)

bundle: SourceBundle = ss.bundle
report: EvidenceReport = scan(bundle)

# ---------------------------------------------------------------------------
# 2. Readiness
# ---------------------------------------------------------------------------

ui.section(2, "What the materials contain")
st.text_input("Company name", key="company_name", help="Pre-filled from the deck. Correct it if needed.")

for err in ss.load_errors:
    st.error(f"Skipped: {err}")
for w in report.extraction_warnings:
    st.warning(w)

m1, m2, m3 = st.columns(3)
m1.metric("Readable pages", f"{report.readable_pages} / {report.total_pages}")
m2.metric("Evidence areas found", sum(r.status == "found" for r in report.results))
m3.metric("Missing", len(report.missing))

st.dataframe(pd.DataFrame([{
    "": STATUS_ICON[r.status],
    "Evidence needed": r.category.label,
    "Where it appears": bundle.format_refs(r.refs) or "not found",
    "Why it matters": r.category.why,
} for r in report.results]), hide_index=True, width="stretch")
st.caption("Keyword screen of the extracted text: a quick guide to coverage, not a judgement of quality.")

critical = {r.category.key for r in report.missing} & {"workflow", "competitors", "pain"}
if critical:
    st.warning("The materials say little about " + ", ".join(
        r.category.label.lower() for r in report.missing if r.category.key in critical) +
        ". The analysis will flag this rather than fill the gap, and may conclude the thesis is not supported.")

# ---------------------------------------------------------------------------
# 3. Generate
# ---------------------------------------------------------------------------

ui.section(3, "Generate")
has_draft = ss.draft is not None
label = "Regenerate analysis" if has_draft else "Generate analysis"
blocked = None
if not _api_key():
    blocked = "Add an Anthropic API key in the sidebar (or ANTHROPIC_API_KEY in .env)."
elif not bundle.readable_refs():
    blocked = "No readable text in the uploads. Upload a text-based PDF or the original PPTX."
elif not ss.company_name.strip():
    blocked = "Enter the company name."
if blocked:
    st.info(blocked)
if has_draft:
    st.caption("Regenerating runs the model again and replaces your edited draft.")

clicked = st.button(label, type="primary", disabled=bool(blocked), width="stretch")
ui.disclosure()
if clicked:
    try:
        with st.spinner("Analysing the materials. This usually takes one to three minutes…"):
            result = generate(bundle, ss.company_name.strip(), report, client=make_client(_api_key()))
    except AnalysisError as exc:
        st.error(str(exc))
    else:
        ss.result = result
        ss.draft = result.analysis.model_copy(deep=True)
        ss.draft_version += 1
        note = result.grounding.note()
        rendered = render_pdf(ss.draft, bundle.format_refs, grounding_note=note)
        ss.pdf_cache = (_pdf_key(ss.draft, note), rendered)
        if config.notifications_enabled():
            with st.spinner("Emailing the result…"):
                ss.email_status = send_result_email(
                    result.analysis, result.grounding, bundle, report, rendered.pdf, model=result.model,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    scale=rendered.scale, forced_shrink=rendered.forced_shrink)
        else:
            ss.email_status = None
        st.rerun()

if ss.draft is None:
    _stop()

result = ss.result
draft: Analysis = ss.draft
if ss.email_status is not None:
    e = ss.email_status
    if e.sent:
        st.success(f"Result emailed to {', '.join(e.recipients)}.")
    else:
        st.warning(f"The result email was not sent ({e.detail}). The analysis below is unaffected.")
v = ss.draft_version
known_refs = set(bundle.pages())


def _parse_refs(text: str) -> list[str]:
    refs = [r for r in re.split(r"[,\s;]+", text.strip()) if r]
    bad = [r for r in refs if r not in known_refs]
    if bad:
        st.caption(f"⚠️ Unknown refs ignored: {', '.join(bad)}")
    return [r for r in refs if r in known_refs]


def _lines(text: str) -> list[str]:
    return [ln.strip(" •-\t") for ln in text.splitlines() if ln.strip(" •-\t")]


# ---------------------------------------------------------------------------
# 4. Review, evidence, export
# ---------------------------------------------------------------------------

ui.section(4, "Review and export")
g = result.grounding
if g.unverified or g.downgraded_from:
    st.warning(f"{g.unverified} quoted claim(s) could not be found in the sources"
               + (f"; thesis support was lowered from '{g.downgraded_from}'" if g.downgraded_from else "")
               + ". See the Evidence tab.")

tab_draft, tab_evidence, tab_pdf = st.tabs(["Draft", "Evidence", "PDF"])

with tab_draft:
    st.caption("Edits are kept for this session and flow straight into the PDF. "
               "Source refs look like D4 (deck slide 4) or S1.2 (supporting file 1, page 2).")
    draft.headline = st.text_area("Headline (investor takeaway)", draft.headline, key=f"{v}_headline", height=70)
    draft.one_line = st.text_input("One-line description", draft.one_line, key=f"{v}_oneline")
    c1, c2 = st.columns([1, 3])
    draft.thesis_support = c1.selectbox("Evidence for the thesis", SUPPORT, SUPPORT.index(draft.thesis_support),
                                        key=f"{v}_support")
    draft.thesis_support_rationale = c2.text_area("Why", draft.thesis_support_rationale,
                                                  key=f"{v}_rationale", height=70)

    for key, title, section in draft.sections():
        st.subheader(title)
        section.body = st.text_area(title, section.body, key=f"{v}_{key}_body", height=130,
                                    label_visibility="collapsed")
        c1, c2 = st.columns([1, 2])
        section.refs = _parse_refs(c1.text_input("Sources", ", ".join(section.refs), key=f"{v}_{key}_refs"))
        c2.caption(f"{len(section.body.split())} words · {bundle.format_refs(section.refs) or 'no source: analyst view'}")

    st.subheader("Alternatives a customer weighs")
    alt_df = pd.DataFrame([{**a.model_dump(exclude={"refs"}), "refs": ", ".join(a.refs)} for a in draft.alternatives],
                          columns=["name", "kind", "how_used", "where_it_breaks", "refs"])
    edited = st.data_editor(alt_df, key=f"{v}_alts", num_rows="dynamic", hide_index=True, width="stretch",
                            column_config={
                                "name": st.column_config.TextColumn("Alternative", required=True),
                                "kind": st.column_config.SelectboxColumn("Kind", options=KINDS, required=True),
                                "how_used": st.column_config.TextColumn("Who uses it, and how"),
                                "where_it_breaks": st.column_config.TextColumn("Where it breaks"),
                                "refs": st.column_config.TextColumn("Sources"),
                            })
    draft.alternatives = [
        Alternative(name=str(r["name"]), kind=r["kind"] if r["kind"] in KINDS else "status quo",
                    how_used=str(r["how_used"] or ""), where_it_breaks=str(r["where_it_breaks"] or ""),
                    refs=[x for x in re.split(r"[,\s;]+", str(r["refs"] or "")) if x in known_refs])
        for r in edited.to_dict("records") if r.get("name") and str(r["name"]).strip() not in ("", "None", "nan")
    ]

    c1, c2 = st.columns(2)
    draft.evidence_gaps = _lines(c1.text_area("What the materials do not show (one per line)",
                                              "\n".join(draft.evidence_gaps), key=f"{v}_gaps", height=120))
    draft.diligence_questions = _lines(c2.text_area("Questions for the founder (one per line)",
                                                    "\n".join(draft.diligence_questions), key=f"{v}_qs", height=120))
    over = length_issues(draft)
    if over:
        st.warning("Over the template's length limits (the PDF will use smaller type):\n\n"
                   + "\n".join(f"- {i}" for i in over))
    if st.button("Discard edits and restore the model's draft"):
        ss.draft = result.analysis.model_copy(deep=True)
        ss.draft_version += 1
        st.rerun()

with tab_evidence:
    c1, c2, c3 = st.columns(3)
    c1.metric("Quoted claims verified", f"{g.verified} / {g.factual}")
    c2.metric("Labelled inferences", sum(c.status == "inference" for c in g.claim_checks))
    c3.metric("Not found in sources", g.unverified)
    for issue in g.issues:
        st.caption(f"• {issue}")

    status_label = {"verified": "✅ verified", "found elsewhere": "✅ found (ref corrected)",
                    "not found": "❌ not found", "inference": "💭 inference"}
    st.subheader("Claims behind the analysis")
    st.dataframe(pd.DataFrame([{
        "Check": status_label[check.status],
        "Claim": claim.statement,
        "Source": bundle.format_refs(claim.refs) or "—",
        "Refs": ", ".join(claim.refs),
        "Quote from source": claim.quote,
    } for claim, check in zip(result.analysis.claims, g.claim_checks)]),
        hide_index=True, width="stretch")

    st.subheader("Where each section comes from")
    st.dataframe(pd.DataFrame(
        [{"Section": title, "Source": bundle.format_refs(sec.refs) or "no source: analyst view",
          "Refs": ", ".join(sec.refs)} for _, title, sec in draft.sections()] +
        [{"Section": f"Alternative: {a.name}", "Source": bundle.format_refs(a.refs) or "no source: analyst view",
          "Refs": ", ".join(a.refs)} for a in draft.alternatives]),
        hide_index=True, width="stretch")

    st.subheader("Source viewer")
    pages = bundle.pages()
    ref = st.selectbox("Page", list(pages), format_func=lambda r: f"{r} · {pages[r][0].location(pages[r][1])}")
    doc, page = pages[ref]
    if page.has_text:
        st.text(page.full_text)
    else:
        st.warning("No extractable text on this page; the analysis could not see it.")

with tab_pdf:
    pdf_key = _pdf_key(draft, g.note())
    if ss.pdf_cache[0] != pdf_key:
        ss.pdf_cache = (pdf_key, render_pdf(draft, bundle.format_refs, grounding_note=g.note()))
    rendered = ss.pdf_cache[1]
    if rendered.forced_shrink or rendered.scale < 0.9:
        st.warning("The draft is long, so the type was reduced to fit one page. Trim a section for a more "
                   "readable memo.")
    safe = re.sub(r"[^\w\-]+", "_", draft.company_name).strip("_") or "Company"
    c1, c2 = st.columns(2)
    c1.download_button("Download one-page PDF", rendered.pdf, file_name=f"{safe}_Competition_Analysis.pdf",
                       mime="application/pdf", type="primary")
    c2.download_button("Download draft (JSON)", draft.model_dump_json(indent=2),
                       file_name=f"{safe}_competition_draft.json", mime="application/json",
                       help="Re-render later with: python cli.py --from-json <file> <deck>")
    st.image(render_preview_png(rendered.pdf), width="stretch")

ui.footer()
