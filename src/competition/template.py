"""The document template: section order, titles, field instructions and limits.

templates/competition_analysis_template.json is the single source of truth for
the memo's structure. The schema (what the model is asked for), the renderer
(what the PDF prints) and the length checks (what the editor is warned about)
all read from it, so changing a title or a word limit is a one-file edit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config

TEMPLATE_PATH = config.PROJECT_ROOT / "templates" / "competition_analysis_template.json"


@lru_cache(maxsize=1)
def load(path: Path = TEMPLATE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def block(name: str, key: str | None = None) -> dict[str, Any]:
    """The first layout block with this block type (and key, when given)."""
    for b in load()["layout"]:
        if b["block"] == name and (key is None or b.get("key") == key):
            return b
    raise KeyError(f"template has no block {name!r} {key or ''}")


def field(key: str) -> dict[str, Any]:
    """A field definition from any block, by key."""
    for b in load()["layout"]:
        if b.get("key") == key:
            return b
        for f in b.get("fields", []) + b.get("columns", []):
            if f.get("key") == key:
                return f
    for f in load()["evidence_record"]["fields"]:
        if f["key"] == key:
            return f
    raise KeyError(f"template has no field {key!r}")


def claim_field(key: str) -> str:
    """Instruction for a field of the evidence record (claims), by key."""
    f = next(f for f in load()["evidence_record"]["fields"] if f["key"] == key)
    limit = f" At most {f['max_words']} words." if "max_words" in f else ""
    return f.get("instruction", "") + limit


def sections() -> list[dict[str, Any]]:
    """Prose sections in print order."""
    return [b for b in load()["layout"] if b["block"] == "section"]


def title(key: str) -> str:
    return field(key)["title"]


def describe(key: str) -> str:
    """The instruction for a field plus its limits, phrased for the model."""
    f = field(key)
    text = f.get("instruction", "")
    if "min_words" in f and "max_words" in f:
        text += f" {f['min_words']}-{f['max_words']} words."
    elif "max_words" in f:
        text += f" At most {f['max_words']} words."
    if "max_words_per_item" in f:
        lo, hi = f["items"]["min"], f["items"]["max"]
        text += f" {lo} to {hi} items, each at most {f['max_words_per_item']} words."
    if "rows" in f:
        text += f" {f['rows']['min']} to {f['rows']['max']} rows."
    return text.strip()


FieldPath = tuple  # location of a text field, e.g. ("wedge", "body"), ("alternatives", 0, "how_used")


@dataclass
class Overrun:
    path: FieldPath
    label: str
    words: int
    limit: int

    def message(self) -> str:
        return f"{self.label}: {self.words} words (limit {self.limit})."


def word_count(text: str) -> int:
    return len(text.split())


def get_text(analysis, path: FieldPath) -> str:
    obj = analysis
    for step in path:
        obj = obj[step] if isinstance(step, int) else getattr(obj, step)
    return obj


def set_text(analysis, path: FieldPath, value: str) -> None:
    *head, last = path
    obj = analysis
    for step in head:
        obj = obj[step] if isinstance(step, int) else getattr(obj, step)
    if isinstance(last, int):
        obj[last] = value
    else:
        setattr(obj, last, value)


def length_overruns(analysis) -> list[Overrun]:
    """Every text field longer than its template word limit, in print order."""
    found: list[Overrun] = []

    def check(path: FieldPath, label: str, limit: int | None) -> None:
        n = word_count(get_text(analysis, path))
        if limit and n > limit:
            found.append(Overrun(path, label, n, limit))

    for key in ("one_line", "headline", "thesis_support_rationale"):
        check((key,), field(key)["label"], field(key).get("max_words"))
    for sec in sections():
        check((sec["key"], "body"), sec["title"], sec.get("max_words"))
    for col in ("how_used", "where_it_breaks"):
        for i, alt in enumerate(analysis.alternatives):
            check(("alternatives", i, col), f"{alt.name} / {field(col)['header']}", field(col).get("max_words"))
    for key in ("evidence_gaps", "diligence_questions"):
        f = field(key)
        for i in range(len(getattr(analysis, key))):
            check((key, i), f"{f['title']} #{i + 1}", f.get("max_words_per_item"))
    return found


def length_issues(analysis) -> list[str]:
    """Human-readable overruns of word and item limits, shown to the editor."""
    issues = [o.message() for o in length_overruns(analysis)]
    for key in ("evidence_gaps", "diligence_questions"):
        f = field(key)
        if len(getattr(analysis, key)) > f["items"]["max"]:
            issues.append(f"{f['title']}: {len(getattr(analysis, key))} items (limit {f['items']['max']}).")
    return issues


def placeholder_analysis():
    """A field-by-field placeholder draft used to render the blank template."""
    from .schema import Alternative, Analysis, Claim, Section

    def ph(key: str) -> str:
        f = field(key)
        return f"[{f.get('label') or f.get('title')}: {describe(key)}]"

    def sec(key: str) -> Section:
        return Section(body=f"[{describe(key)}]", refs=[])

    return Analysis(
        company_name="[Company Name]",
        one_line=ph("one_line"),
        headline=ph("headline"),
        status_quo=sec("status_quo"),
        failure_modes=sec("failure_modes"),
        alternatives=[Alternative(name=f"[Alternative {i}]", kind=k,
                                  how_used=f"[{describe('how_used')}]",
                                  where_it_breaks=f"[{describe('where_it_breaks')}]", refs=[])
                      for i, k in ((1, "status quo"), (2, "incumbent"), (3, "adjacent tool"))],
        market_structure=sec("market_structure"),
        wedge=sec("wedge"),
        thesis_support="partially supported",
        thesis_support_rationale=f"[{describe('thesis_support_rationale')}]",
        evidence_gaps=[f"[Gap {i}: {field('evidence_gaps')['instruction']}]" for i in (1, 2, 3)],
        diligence_questions=[f"[Question {i}: {field('diligence_questions')['instruction']}]" for i in (1, 2)],
        claims=[Claim(statement="[Claim]", refs=[], quote="", basis="inference from sources")],
    )


def render_blank(out: Path) -> Path:
    """Write the empty template PDF: structure and field instructions, no data."""
    from .render import render_pdf

    options = " / ".join(o.capitalize() for o in field("thesis_support")["options"])
    pattern = "[Deck s.#; Supporting document p.#]"
    pdf = render_pdf(placeholder_analysis(), format_refs=lambda refs: pattern,
                     grounding_note="[Grounding note: N of M quoted claims verified against the source text.]",
                     date_text="[DATE]", support_label=f"[{options}]").pdf
    out.write_bytes(pdf)
    return out
