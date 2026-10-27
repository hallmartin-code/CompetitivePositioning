# Competition Positioning

Turns an investor pitch deck (plus optional supporting material) into a one-page
competition analysis for a seed-stage investment memo: how customers solve the
problem today, why that fails, how the market is organized, and where an
investable wedge emerges. When the materials do not support a thesis, the memo
says so instead of inventing one.

## Run it

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # then add ANTHROPIC_API_KEY
.venv\Scripts\streamlit run app.py
```

Headless:

```powershell
.venv\Scripts\python cli.py samples\Cairnly_Seed_Deck.pptx --support samples\Cairnly_customer_interviews.pdf
.venv\Scripts\python cli.py deck.pdf --from-json edited_draft.json   # re-render, no model call
```

## How it works

1. **Ingest** (`src/competition/ingest.py`): PDF via PyMuPDF, PPTX via python-pptx
   (text, tables, chart series, speaker notes). Legacy `.ppt` is converted with
   LibreOffice if installed; otherwise the user is told to save as PPTX or PDF.
   Every page gets a ref (`D4` = deck slide 4, `S1.2` = supporting file 1 page 2).
   Image-only slides and scanned PDFs are reported, never silently skipped.
2. **Evidence screen** (`evidence.scan`): before any model call, a keyword screen
   shows which evidence a competition analysis needs (current workflow, failure
   points, alternatives, buyer, differentiation, traction, market, pricing) and
   where it appears.
3. **Analysis** (`analyzer.py`): one streamed Claude call (`claude-opus-5-5`,
   adaptive thinking, structured output into `schema.Analysis`, server-side
   refusal fallback). The prompt asks for a venture partner's voice, forbids
   facts from outside the sources, and requires refs and verbatim quotes.
4. **Grounding** (`evidence.ground`): unknown refs are removed, each quote is
   checked against the extracted text (misattributed quotes are re-pointed), and
   a "supported" rating resting on unverified quotes is lowered.
5. **Render** (`render.py`): ReportLab, TEN Capital palette, Open Sans, CLAUDE.md
   footer with logo, concise source lines under each section. Tries descending
   type scales and keeps the largest that fits one page.

The Streamlit app keeps uploads, the model output and your edits in session
state, so you can revise and re-export without re-uploading or re-running the
model. The Evidence tab lists every claim, its source location, quote and
verification status, plus a page-by-page source viewer.

## Document template

`templates/competition_analysis_template.json` defines the memo's structure:
block order, section titles, field instructions, word and item limits, enum
options, table columns, source-line and footer formats. It holds no company
data. The schema sent to the model, the PDF layout and the editor's
length warnings all read from it, so a title, limit or section order changes
in one place. `templates/competition_analysis_template.pdf` is the same template
rendered with placeholders; regenerate it with `python cli.py --blank-template`.

Renaming a field or adding a new one also needs a matching field in
`src/competition/schema.py`; `tests/test_template.py` fails if the two drift.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

Tests use a fake Anthropic client and fictional sample materials built by
`tests/make_sample_deck.py` (Cairnly, plus a deliberately thin deck). The `.ppt`
test runs only where LibreOffice is installed.

## Result emails (Resend)

Every fresh generation, in the app or the CLI, is emailed through Resend to
`Info@tencapital.group`. The email contains a summary (headline, evidence rating
and rationale, files analysed, claim-verification count, evidence gaps, founder
questions, warnings) and three attachments: the one-page PDF as generated, the
draft JSON and the evidence check. Edits, reruns and `--from-json` re-renders do
not send. Use `--no-email` on the CLI to skip a send.

Turn it on by adding `RESEND_API_KEY` to `.env`. `NOTIFY_EMAIL_TO` (comma-separated),
`NOTIFY_EMAIL_FROM` (must be on a domain verified in Resend) and
`EMAIL_NOTIFICATIONS=false` are optional. A failed send is shown as a warning
and never affects the analysis; tests can never send real email.
