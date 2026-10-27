"""Headless runner: deck in, one-page PDF out.

    python cli.py samples/Cairnly_Seed_Deck.pptx --support samples/Cairnly_customer_interviews.pdf
    python cli.py deck.pdf --company "Acme" --out output/acme.pdf
    python cli.py deck.pdf --from-json acme_draft.json      # re-render an edited draft, no model call
    python cli.py --blank-template                          # render the empty document template
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from competition import config  # noqa: E402
from competition.analyzer import AnalysisError, generate  # noqa: E402
from competition.evidence import ground, scan  # noqa: E402
from competition.ingest import IngestError, guess_company_name, load_bundle  # noqa: E402
from competition.notify import send_result_email  # noqa: E402
from competition.render import render_pdf  # noqa: E402
from competition.schema import Analysis  # noqa: E402
from competition.template import TEMPLATE_PATH, length_issues, render_blank  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck", type=Path, nargs="?")
    ap.add_argument("--support", type=Path, nargs="*", default=[])
    ap.add_argument("--company")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--from-json", type=Path, help="Render a saved draft instead of calling the model.")
    ap.add_argument("--no-email", action="store_true", help="Do not email the result, even if Resend is configured.")
    ap.add_argument("--blank-template", action="store_true",
                    help="Render the empty template with field placeholders and exit.")
    args = ap.parse_args(argv)

    if args.blank_template:
        print(f"Wrote {render_blank(args.out or TEMPLATE_PATH.with_suffix('.pdf'))}")
        return 0
    if args.deck is None:
        ap.error("a deck is required unless --blank-template is given")

    try:
        bundle, errors = load_bundle((args.deck.name, args.deck.read_bytes()),
                                     [(p.name, p.read_bytes()) for p in args.support])
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = scan(bundle)
    for msg in errors:
        print(f"skipped: {msg}", file=sys.stderr)
    for w in report.extraction_warnings:
        print(f"warning: {w}", file=sys.stderr)
    for r in report.results:
        print(f"  [{r.status:>7}] {r.category.label}: {bundle.format_refs(r.refs) or '-'}")

    company = args.company or guess_company_name(bundle)
    if args.from_json:
        analysis = Analysis.model_validate_json(args.from_json.read_text(encoding="utf-8"))
        grounding = ground(analysis, bundle)
    else:
        print(f"Analysing {company} with {config.MODEL}...")
        try:
            result = generate(bundle, company, report)
        except AnalysisError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        analysis, grounding = result.analysis, result.grounding
        print(f"  tokens: {result.input_tokens:,} in / {result.output_tokens:,} out")

    for issue in length_issues(analysis):
        print(f"length: {issue}", file=sys.stderr)
    for issue in grounding.issues:
        print(f"grounding: {issue}", file=sys.stderr)

    safe = re.sub(r"[^\w\-]+", "_", analysis.company_name).strip("_") or "Company"
    out = args.out or config.OUTPUT_DIR / f"{safe}_Competition_Analysis.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_pdf(analysis, bundle.format_refs, grounding_note=grounding.note())
    out.write_bytes(rendered.pdf)
    out.with_suffix(".json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    audit = {"grounding_note": grounding.note(), "issues": grounding.issues,
             "claims": [{"claim": c.statement, "refs": c.refs, "check": k.status}
                        for c, k in zip(analysis.claims, grounding.claim_checks)]}
    out.with_suffix(".evidence.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(f"Thesis support: {analysis.thesis_support}. {grounding.note()}")
    print(f"Wrote {out} (type scale {rendered.scale:.2f}{', shrunk to fit' if rendered.forced_shrink else ''})")

    # Only fresh generations are emailed; re-rendering a saved draft is not a new result.
    if not args.from_json and not args.no_email:
        outcome = send_result_email(analysis, grounding, bundle, report, rendered.pdf, model=result.model,
                                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                    scale=rendered.scale, forced_shrink=rendered.forced_shrink)
        if outcome.sent:
            print(f"Emailed result to {', '.join(outcome.recipients)} (id {outcome.detail})")
        else:
            print(f"email: not sent ({outcome.detail})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
