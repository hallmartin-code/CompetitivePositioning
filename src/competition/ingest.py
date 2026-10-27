"""Turn uploaded decks and supporting files into page-addressable source text.

Every page gets a short reference the model cites and the memo prints:

    D4    primary deck, slide/page 4
    S1.2  first supporting document, page 2

Extraction problems (image-only slides, scanned PDFs, failed conversions) are
collected as warnings rather than raised, so the user sees exactly what the
analysis could and could not read before any model call is made.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from . import config

SUPPORTED_EXTENSIONS = (".pdf", ".pptx", ".ppt")

PPT_INSTRUCTION = (
    "Legacy .ppt files need LibreOffice for conversion, and it was not found on "
    "this machine. Open the file in PowerPoint or Keynote and use File > Save As "
    "to save it as .pptx or .pdf, then upload that file instead. (Or install "
    "LibreOffice, or set LIBREOFFICE_PATH to soffice.)"
)


class IngestError(Exception):
    """A file that cannot be read at all. The message is shown to the user."""


@dataclass
class SourcePage:
    ref: str            # "D4" / "S1.2"
    number: int         # 1-based page or slide number
    text: str
    notes: str = ""     # speaker notes (PPTX only)

    @property
    def has_text(self) -> bool:
        return len((self.text + self.notes).strip()) >= config.MIN_PAGE_CHARS

    @property
    def full_text(self) -> str:
        if self.notes.strip():
            return f"{self.text}\n[Speaker notes] {self.notes}"
        return self.text


@dataclass
class SourceDocument:
    doc_id: str               # "D" for the primary deck, "S1", "S2" ... otherwise
    filename: str
    kind: str                 # "pdf" | "pptx" | "ppt"
    pages: list[SourcePage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title_guess: str = ""

    @property
    def is_primary(self) -> bool:
        return self.doc_id == "D"

    @property
    def unit(self) -> str:
        return "slide" if self.kind in ("pptx", "ppt") else "page"

    @property
    def short_name(self) -> str:
        """Concise label used in memo source lines."""
        if self.is_primary:
            return "Deck"
        stem = re.sub(r"[_\-]+", " ", Path(self.filename).stem).strip()
        return stem if len(stem) <= 32 else stem[:30].rstrip() + "…"

    def page_ref(self, number: int) -> str:
        return f"D{number}" if self.is_primary else f"{self.doc_id}.{number}"

    def location(self, page: SourcePage) -> str:
        return f"{self.short_name}, {self.unit} {page.number}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_document(filename: str, data: bytes, doc_id: str) -> SourceDocument:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise IngestError(
            f"{filename}: unsupported file type '{ext or 'none'}'. "
            "Upload a PDF or PPTX (legacy PPT is converted when LibreOffice is available)."
        )
    if not data:
        raise IngestError(f"{filename}: the file is empty.")

    if ext == ".pdf":
        doc = _load_pdf(filename, data, doc_id)
    elif ext == ".pptx":
        doc = _load_pptx(filename, data, doc_id, kind="pptx")
    else:
        pptx_bytes = convert_ppt_to_pptx(filename, data)
        doc = _load_pptx(filename, pptx_bytes, doc_id, kind="ppt")
        doc.warnings.insert(0, f"{filename}: converted from legacy .ppt with LibreOffice; "
                               "check the evidence view for any slide that looks garbled.")

    _add_coverage_warnings(doc)
    return doc


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _load_pdf(filename: str, data: bytes, doc_id: str) -> SourceDocument:
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several unrelated types
        raise IngestError(f"{filename}: not a readable PDF ({exc}).") from exc
    if pdf.needs_pass:
        raise IngestError(f"{filename}: the PDF is password-protected. Remove the password and re-upload.")

    doc = SourceDocument(doc_id=doc_id, filename=filename, kind="pdf")
    with pdf:
        for i, page in enumerate(pdf, start=1):
            text = _clean(page.get_text("text"))
            doc.pages.append(SourcePage(ref=doc.page_ref(i), number=i, text=text))
        if pdf.page_count:
            doc.title_guess = _pdf_title_guess(pdf[0])
    return doc


def _pdf_title_guess(page: "fitz.Page") -> str:
    """The largest-font line on the first page is usually the company name."""
    best_size, best_text = 0.0, ""
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", [])).strip()
            size = max((span["size"] for span in line.get("spans", [])), default=0)
            if text and size > best_size and len(text) <= 60:
                best_size, best_text = size, text
    return best_text


# ---------------------------------------------------------------------------
# PPTX / PPT
# ---------------------------------------------------------------------------

def _load_pptx(filename: str, data: bytes, doc_id: str, kind: str) -> SourceDocument:
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise IngestError(f"{filename}: not a readable PowerPoint file ({exc}).") from exc

    doc = SourceDocument(doc_id=doc_id, filename=filename, kind=kind)
    for i, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            parts.extend(_shape_text(shape))
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = _clean(slide.notes_slide.notes_text_frame.text)
        doc.pages.append(SourcePage(ref=doc.page_ref(i), number=i,
                                    text=_clean("\n".join(parts)), notes=notes))
        if i == 1:
            doc.title_guess = _pptx_title_guess(slide)
    return doc


def _shape_text(shape) -> list[str]:
    out: list[str] = []
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:
            out.extend(_shape_text(child))
        return out
    if shape.has_text_frame and shape.text_frame.text.strip():
        out.append(shape.text_frame.text)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                out.append(" | ".join(cells))
    if getattr(shape, "has_chart", False) and shape.has_chart:
        chart = shape.chart
        if chart.has_title and chart.chart_title.has_text_frame:
            out.append("[Chart] " + chart.chart_title.text_frame.text)
        try:
            for plot in chart.plots:
                cats = list(plot.categories)
                for series in plot.series:
                    pairs = ", ".join(f"{c}: {v}" for c, v in zip(cats, series.values))
                    out.append(f"[Chart series {series.name}] {pairs}")
        except Exception:
            pass  # chart data is a bonus; a malformed chart must not fail the slide
    return out


def _pptx_title_guess(slide) -> str:
    title = slide.shapes.title
    if title is not None and title.text_frame.text.strip():
        return title.text_frame.text.strip().splitlines()[0]
    for shape in slide.shapes:
        if shape.has_text_frame and shape.text_frame.text.strip():
            return shape.text_frame.text.strip().splitlines()[0]
    return ""


def find_libreoffice() -> str | None:
    candidates = [config.LIBREOFFICE_PATH, shutil.which("soffice"), shutil.which("libreoffice")]
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(str(Path(base) / "LibreOffice" / "program" / "soffice.exe"))
    candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def convert_ppt_to_pptx(filename: str, data: bytes) -> bytes:
    soffice = find_libreoffice()
    if not soffice:
        raise IngestError(f"{filename}: {PPT_INSTRUCTION}")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.ppt"
        src.write_bytes(data)
        # A private profile dir stops a running desktop LibreOffice from
        # swallowing the headless call.
        profile = Path(tmp, "profile").as_uri()
        cmd = [soffice, f"-env:UserInstallation={profile}", "--headless",
               "--convert-to", "pptx", "--outdir", tmp, str(src)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=config.PPT_CONVERT_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IngestError(f"{filename}: LibreOffice conversion failed ({exc}). "
                              "Save the file as .pptx or .pdf and upload that instead.") from exc
        out = Path(tmp) / "input.pptx"
        if not out.exists():
            raise IngestError(f"{filename}: LibreOffice could not convert this file. "
                              "Save it as .pptx or .pdf and upload that instead.")
        return out.read_bytes()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = text.replace("\x0b", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _add_coverage_warnings(doc: SourceDocument) -> None:
    if not doc.pages:
        doc.warnings.append(f"{doc.filename}: no pages or slides found.")
        return
    empty = [p.number for p in doc.pages if not p.has_text]
    if len(empty) == len(doc.pages):
        doc.warnings.append(
            f"{doc.filename}: no extractable text on any {doc.unit}. It is probably "
            "scanned or exported as images, so nothing in it can be analyzed. Export a "
            "text-based PDF or upload the original PPTX."
        )
    elif empty:
        nums = ", ".join(str(n) for n in empty)
        which = f"{doc.unit} {nums} has" if len(empty) == 1 else f"{doc.unit}s {nums} have"
        doc.warnings.append(
            f"{doc.filename}: {which} little or no extractable text "
            "(usually an image, chart or screenshot). That content is invisible to the analysis."
        )


# ---------------------------------------------------------------------------
# A set of documents analysed together
# ---------------------------------------------------------------------------

@dataclass
class SourceBundle:
    documents: list[SourceDocument]

    @property
    def primary(self) -> SourceDocument | None:
        return next((d for d in self.documents if d.is_primary), None)

    @property
    def warnings(self) -> list[str]:
        return [w for d in self.documents for w in d.warnings]

    def pages(self) -> dict[str, tuple[SourceDocument, SourcePage]]:
        return {p.ref: (d, p) for d in self.documents for p in d.pages}

    def readable_refs(self) -> set[str]:
        return {ref for ref, (_, p) in self.pages().items() if p.has_text}

    def total_chars(self) -> int:
        return sum(len(p.full_text) for d in self.documents for p in d.pages)

    def format_refs(self, refs: list[str]) -> str:
        """["D3", "D7", "S1.2"] -> "Deck s.3, 7; interview notes p.2"."""
        index = self.pages()
        grouped: dict[str, list[int]] = {}
        order: list[SourceDocument] = []
        for ref in refs:
            if ref not in index:
                continue
            doc, page = index[ref]
            if doc.doc_id not in grouped:
                grouped[doc.doc_id] = []
                order.append(doc)
            if page.number not in grouped[doc.doc_id]:
                grouped[doc.doc_id].append(page.number)
        parts = []
        for doc in order:
            nums = ", ".join(str(n) for n in sorted(grouped[doc.doc_id]))
            abbrev = "s." if doc.unit == "slide" else "p."
            parts.append(f"{doc.short_name} {abbrev}{nums}")
        return "; ".join(parts)


def load_bundle(primary: tuple[str, bytes], supporting: list[tuple[str, bytes]]) -> tuple[SourceBundle, list[str]]:
    """Load the deck and supporting files.

    A supporting file that fails is reported and skipped; a failed primary deck
    raises, since there is nothing to analyse without it.
    """
    errors: list[str] = []
    docs = [load_document(primary[0], primary[1], "D")]
    for i, (name, data) in enumerate(supporting, start=1):
        try:
            docs.append(load_document(name, data, f"S{i}"))
        except IngestError as exc:
            errors.append(str(exc))
    return SourceBundle(docs), errors


def guess_company_name(bundle: SourceBundle) -> str:
    doc = bundle.primary
    if doc is None:
        return ""
    guess = doc.title_guess.strip()
    if guess and len(guess.split()) <= 5:
        return guess
    stem = re.sub(r"[_\-]+", " ", Path(doc.filename).stem)
    stem = re.sub(r"(?i)\b(pitch|deck|investor|presentation|final|draft|v\d+[\d.]*|\d{4})\b", "", stem)
    return re.sub(r"\s+", " ", stem).strip() or guess
