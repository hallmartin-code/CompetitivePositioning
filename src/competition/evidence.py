"""Evidence checks on both sides of the model call.

Before generation, ``scan`` looks for the kinds of evidence a competition
analysis needs and reports where each appears, so a thin deck is visible before
any money is spent on a model call. It is a keyword screen, deliberately
conservative in what it calls "found", and says so in the UI.

After generation, ``ground`` checks every reference and quote the model returned
against the extracted text. Unknown references are removed, quotes that cannot
be found are flagged, and a "supported" rating resting on unverified claims is
downgraded rather than passed through.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .ingest import SourceBundle
from .schema import Analysis


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    why: str
    pattern: re.Pattern


def _rx(*terms: str) -> re.Pattern:
    return re.compile(r"(?i)(" + "|".join(terms) + r")")


CATEGORIES = [
    Category("workflow", "Current customer workflow",
             "Needed to explain how customers solve the problem today.",
             _rx(r"\btoday\b", r"\bcurrently\b", r"status quo", r"\bmanual(ly)?\b", r"spreadsheet",
                 r"\bexcel\b", r"\blegacy\b", r"workaround", r"in-house", r"pen and paper",
                 r"\bphone calls?\b", r"\bemail\b", r"existing (process|tools?|workflow)")),
    Category("pain", "Why current approaches fail",
             "Needed to show the status quo breaks, not just that it is imperfect.",
             _rx(r"\bwaste", r"\berrors?\b", r"\bdelays?\b", r"\bslow\b", r"expensive", r"\bchurn",
                 r"frustrat", r"inefficien", r"\blost\b", r"\bhours?\b", r"\bpain\b", r"stockouts?",
                 r"\bcosts? (of|too)\b", r"\bfails?\b", r"\bran out\b", r"reschedul", r"by hand",
                 r"retail price", r"no time|don't have time")),
    Category("competitors", "Named alternatives or competitors",
             "Needed to map who else the customer can buy from.",
             _rx(r"competit", r"\balternatives?\b", r"incumbent", r"\bvs\.?\b", r"\bversus\b",
                 r"compared (to|with)", r"landscape", r"\bplayers\b")),
    Category("buyer", "Customer and buyer",
             "Needed to say who feels the pain and who signs.",
             _rx(r"\bcustomers?\b", r"\bbuyers?\b", r"\bpersona", r"decision[- ]maker", r"\bowners?\b",
                 r"\b(cfo|cio|coo|ceo|vp)\b", r"procurement", r"\bsegment", r"office manager")),
    Category("differentiation", "Differentiation or defensibility",
             "Needed to argue the wedge can widen rather than be copied.",
             _rx(r"\bunlike\b", r"proprietary", r"\bpatent", r"\bmoat\b", r"defensib", r"\bunique",
                 r"differentiat", r"advantage", r"network effect", r"switching cost", r"data (asset|set)")),
    Category("traction", "Traction or customer validation",
             "Needed to show customers choose this over the status quo.",
             _rx(r"\bpilots?\b", r"\bLOIs?\b", r"\b[AM]RR\b", r"\brevenue\b", r"\bpaying\b", r"waitlist",
                 r"retention", r"\bcontracts?\b", r"\bsigned\b", r"\bcase stud")),
    Category("market", "Market size and structure",
             "Needed to describe how the market is organized and how big it is.",
             _rx(r"\bTAM\b", r"\bSAM\b", r"\bSOM\b", r"market size", r"\$\s?\d[\d.,]*\s?(B|bn|billion|M|mm|million)\b",
                 r"fragmented", r"consolidat", r"\bCAGR\b", r"\bdistributors?\b", r"\bchannel")),
    Category("pricing", "Pricing and business model",
             "Needed to compare economics against the alternatives.",
             _rx(r"\bpric(e|ing)\b", r"per (seat|user|month|location|clinic|practice)", r"subscription",
                 r"\bACV\b", r"\bfees?\b", r"\bSaaS\b", r"take rate", r"\bmargins?\b", r"/\s?mo(nth)?\b")),
]


@dataclass
class CategoryResult:
    category: Category
    refs: list[str]

    dedicated: bool = False   # a page is titled for this topic, e.g. a "Competition" slide

    @property
    def status(self) -> str:
        if not self.refs:
            return "missing"
        return "found" if self.dedicated or len(self.refs) > 1 else "thin"


@dataclass
class EvidenceReport:
    results: list[CategoryResult]
    extraction_warnings: list[str]
    readable_pages: int
    total_pages: int

    @property
    def missing(self) -> list[CategoryResult]:
        return [r for r in self.results if r.status == "missing"]

    @property
    def thin(self) -> list[CategoryResult]:
        return [r for r in self.results if r.status == "thin"]


def scan(bundle: SourceBundle) -> EvidenceReport:
    pages = bundle.pages()
    results = []
    for cat in CATEGORIES:
        refs = [ref for ref, (_, page) in pages.items() if page.has_text and cat.pattern.search(page.full_text)]
        # The first line of a slide is its title; a match there means the deck
        # gives the topic a page of its own.
        dedicated = any(cat.pattern.search(pages[r][1].text.split("\n", 1)[0]) for r in refs)
        results.append(CategoryResult(cat, refs, dedicated))
    readable = sum(1 for _, page in pages.values() if page.has_text)
    return EvidenceReport(results, bundle.warnings, readable, len(pages))


# ---------------------------------------------------------------------------
# Post-generation grounding
# ---------------------------------------------------------------------------

@dataclass
class ClaimCheck:
    index: int
    status: str        # "verified" | "found elsewhere" | "not found" | "inference"
    found_in: list[str] = field(default_factory=list)


@dataclass
class GroundingReport:
    claim_checks: list[ClaimCheck]
    issues: list[str]
    downgraded_from: str | None = None

    @property
    def verified(self) -> int:
        return sum(1 for c in self.claim_checks if c.status in ("verified", "found elsewhere"))

    @property
    def factual(self) -> int:
        return sum(1 for c in self.claim_checks if c.status != "inference")

    @property
    def unverified(self) -> int:
        return sum(1 for c in self.claim_checks if c.status == "not found")

    def note(self) -> str:
        """One sentence for the memo's fine print."""
        if not self.factual:
            return "No quoted claims were available to verify against the source text."
        return f"{self.verified} of {self.factual} quoted claims were verified against the source text."


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = (text.replace("’", "'").replace("‘", "'").replace("“", '"')
                .replace("”", '"').replace("–", "-").replace("—", "-").replace("…", "..."))
    text = re.sub(r"[^\w$%.'\-/ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _quote_in(quote: str, haystack: str) -> bool:
    q = normalize(quote).strip(" .'\"")
    if not q:
        return False
    h = normalize(haystack)
    if q in h:
        return True
    # Models sometimes elide the middle of a quote with "..." - accept when
    # every substantial fragment is present.
    fragments = [f.strip() for f in re.split(r"\.\.\.+", q) if len(f.strip()) >= 12]
    return len(fragments) > 1 and all(f in h for f in fragments)


def ground(analysis: Analysis, bundle: SourceBundle) -> GroundingReport:
    """Validate refs and quotes in place; return what was found."""
    pages = bundle.pages()
    known = set(pages)
    issues: list[str] = []

    def clean(refs: list[str], where: str) -> list[str]:
        good = [r.strip() for r in refs if r.strip() in known]
        bad = [r for r in refs if r.strip() not in known]
        if bad:
            issues.append(f"{where}: removed unknown reference(s) {', '.join(bad)}.")
        return list(dict.fromkeys(good))

    for key, title, section in analysis.sections():
        section.refs = clean(section.refs, title)
        if not section.refs:
            issues.append(f"{title}: no source supports this section; treat it as analyst opinion.")
    for alt in analysis.alternatives:
        alt.refs = clean(alt.refs, f"Alternative '{alt.name}'")

    checks: list[ClaimCheck] = []
    for i, claim in enumerate(analysis.claims):
        claim.refs = clean(claim.refs, f"Claim {i + 1}")
        if claim.basis == "inference from sources" or not claim.quote.strip():
            checks.append(ClaimCheck(i, "inference"))
            continue
        if any(_quote_in(claim.quote, pages[r][1].full_text) for r in claim.refs):
            checks.append(ClaimCheck(i, "verified", claim.refs))
            continue
        elsewhere = [r for r, (_, p) in pages.items() if _quote_in(claim.quote, p.full_text)]
        if elsewhere:
            checks.append(ClaimCheck(i, "found elsewhere", elsewhere))
            issues.append(f"Claim {i + 1}: quote found on {', '.join(elsewhere)}, not on the cited page; reference corrected.")
            claim.refs = elsewhere
        else:
            checks.append(ClaimCheck(i, "not found"))
            issues.append(f"Claim {i + 1}: quoted text could not be found in the sources. Verify before relying on it.")

    report = GroundingReport(checks, issues)
    factual = report.factual
    ratio = report.verified / factual if factual else 0.0
    if analysis.thesis_support == "supported" and (factual < 3 or ratio < 0.6):
        report.downgraded_from = "supported"
        analysis.thesis_support = "partially supported"
        issues.append(
            f"Thesis support lowered from 'supported' to 'partially supported': only "
            f"{report.verified} of {factual} quoted claims could be verified in the sources."
        )
    return report
