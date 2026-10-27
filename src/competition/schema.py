"""The structured analysis the model returns and the user edits.

Field descriptions are sent to the model as part of the JSON schema, so they
double as per-field writing instructions. They come from the document template
(templates/competition_analysis_template.json) so structure lives in one place.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import template as T

ThesisSupport = Literal["supported", "partially supported", "not supported"]
AlternativeKind = Literal["status quo", "incumbent", "direct competitor", "adjacent tool", "in-house build"]
ClaimBasis = Literal["stated in sources", "inference from sources"]


class Section(BaseModel):
    body: str = Field(description="Memo prose for this section. Plain sentences, no bullets or markdown.")
    refs: list[str] = Field(description="Page refs (e.g. 'D4', 'S1.2') that support this section. Empty only if nothing in the sources supports it.")


class Alternative(BaseModel):
    name: str = Field(description=T.describe("name"))
    kind: AlternativeKind
    how_used: str = Field(description=T.describe("how_used"))
    where_it_breaks: str = Field(description=T.describe("where_it_breaks"))
    refs: list[str]


class Claim(BaseModel):
    statement: str = Field(description=T.claim_field("statement"))
    refs: list[str] = Field(description=T.claim_field("refs"))
    quote: str = Field(description=T.claim_field("quote"))
    basis: ClaimBasis


class Analysis(BaseModel):
    company_name: str
    one_line: str = Field(description=T.describe("one_line"))
    headline: str = Field(description=T.describe("headline"))
    status_quo: Section = Field(description=T.describe("status_quo"))
    failure_modes: Section = Field(description=T.describe("failure_modes"))
    alternatives: list[Alternative] = Field(description=T.describe("alternatives"))
    market_structure: Section = Field(description=T.describe("market_structure"))
    wedge: Section = Field(description=T.describe("wedge"))
    thesis_support: ThesisSupport = Field(description=T.describe("thesis_support"))
    thesis_support_rationale: str = Field(description=T.describe("thesis_support_rationale"))
    evidence_gaps: list[str] = Field(description=T.describe("evidence_gaps"))
    diligence_questions: list[str] = Field(description=T.describe("diligence_questions"))
    claims: list[Claim] = Field(description="The key factual claims the analysis rests on, "
                                            f"{T.load()['evidence_record']['claims']['min']} to "
                                            f"{T.load()['evidence_record']['claims']['max']}.")

    def sections(self) -> list[tuple[str, str, Section]]:
        """Prose sections in template order: (key, title, section)."""
        return [(s["key"], s["title"], getattr(self, s["key"])) for s in T.sections()]
