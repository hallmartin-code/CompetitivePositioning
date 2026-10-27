"""Email each generated analysis via Resend.

This is the one place the app sends deck-derived content anywhere other than the
Anthropic API, so it is deliberately narrow:

* it runs only when RESEND_API_KEY is set and EMAIL_NOTIFICATIONS is on;
* it never raises: a failed send loses an email, not the user's analysis;
* it refuses to send under pytest, so no test can mail a real inbox.

The Resend REST API is a single JSON POST, so it is called with urllib rather
than adding an SDK dependency, matching the other TEN Capital tools.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Any, Sequence

from . import config
from .evidence import EvidenceReport, GroundingReport
from .ingest import SourceBundle
from .schema import Analysis
from .template import length_issues

RESEND_ENDPOINT = "https://api.resend.com/emails"
# Resend sits behind Cloudflare, which rejects urllib's default User-Agent (403, error 1010).
USER_AGENT = "CompetitivePositioning/1.0 (+https://tencapital.group)"
REQUEST_TIMEOUT = 20.0
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0
# Resend caps a message at 40 MB; stay well under it after base64 inflation.
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024


class ResendError(RuntimeError):
    """Raised inside the mailer; never propagated to callers of send_result_email."""


@dataclass
class Attachment:
    filename: str
    content: bytes

    def to_payload(self) -> dict[str, str]:
        return {"filename": self.filename, "content": base64.b64encode(self.content).decode("ascii")}


@dataclass
class EmailOutcome:
    sent: bool
    detail: str          # message id when sent, otherwise why not
    recipients: list[str]


class ResendMailer:
    def __init__(self, api_key: str | None = None, timeout: float = REQUEST_TIMEOUT) -> None:
        self.api_key = (api_key or config.RESEND_API_KEY or "").strip()
        if not self.api_key:
            raise ResendError("RESEND_API_KEY is not configured.")
        self.timeout = timeout

    def send(self, *, sender: str, to: Sequence[str], subject: str, html: str, text: str,
             attachments: Sequence[Attachment] = ()) -> str:
        """Send one message and return the Resend message id."""
        payload: dict[str, Any] = {"from": sender, "to": list(to), "subject": subject, "html": html, "text": text}
        if attachments:
            payload["attachments"] = [a.to_payload() for a in attachments]
        body = json.dumps(payload).encode("utf-8")
        last_error = "unknown error"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(RESEND_ENDPOINT, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            })
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return str(json.loads(response.read() or b"{}").get("id", ""))
            except urllib.error.HTTPError as exc:
                detail = (exc.read() or b"")[:300].decode("utf-8", "replace")
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code != 429 and exc.code < 500:
                    break  # a 4xx other than rate limiting will not succeed on retry
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        raise ResendError(last_error)


# ---------------------------------------------------------------------------
# Message composition (TEN Capital light palette)
# ---------------------------------------------------------------------------

_INK, _BODY, _MUTED = "#000000", "#4B4F58", "#7A7A7A"
_CORAL, _TEAL, _AMBER = "#ED5644", "#4FC4D6", "#F1A31F"
_CREAM, _GRAY, _RULE = "#FFFDF6", "#F7F7F7", "#CBD6E2"
_SANS = "Inter,Open Sans,Arial,Helvetica,sans-serif"
_SUPPORT_COLOR = {"supported": _TEAL, "partially supported": _AMBER, "not supported": _CORAL}
_SUPPORT_TAG = {"supported": "SUPPORTED", "partially supported": "PARTIAL", "not supported": "NOT SUPPORTED"}


def safe_name(company: str) -> str:
    return re.sub(r"[^\w\-]+", "_", company).strip("_") or "Company"


def _rows(items: list[tuple[str, str]]) -> str:
    return "".join(
        f'<tr><td style="padding:3px 14px 3px 0;color:{_MUTED};font:500 12px/1.5 {_SANS};'
        f'white-space:nowrap;vertical-align:top">{escape(k)}</td>'
        f'<td style="padding:3px 0;color:{_INK};font:600 13px/1.5 {_SANS}">{escape(v)}</td></tr>'
        for k, v in items)


def _bullets(title: str, items: list[str], colour: str) -> str:
    if not items:
        return ""
    lis = "".join(f'<li style="margin:2px 0">{escape(i)}</li>' for i in items[:10])
    return (f'<div style="margin-top:18px"><div style="font:600 11px/1.4 {_SANS};letter-spacing:.08em;'
            f'text-transform:uppercase;color:{colour};margin-bottom:4px">{escape(title)}</div>'
            f'<ul style="margin:0;padding-left:18px;color:{_BODY};font:400 13px/1.6 {_SANS}">{lis}</ul></div>')


def build_message(analysis: Analysis, grounding: GroundingReport, bundle: SourceBundle,
                  report: EvidenceReport, *, model: str, input_tokens: int, output_tokens: int,
                  scale: float, forced_shrink: bool) -> tuple[str, str, str]:
    """Return (subject, html, text) for one generated analysis."""
    company = analysis.company_name
    support = analysis.thesis_support
    needs_review = bool(grounding.unverified or grounding.downgraded_from or forced_shrink)
    tag = _SUPPORT_TAG.get(support, support.upper()) + (" · REVIEW" if needs_review else "")
    subject = f"[{tag}] Competition analysis: {company}"

    files = [d.filename for d in bundle.documents]
    facts = [
        ("Company", company),
        ("Deck", files[0]),
        ("Supporting files", ", ".join(files[1:]) or "none"),
        ("Readable pages", f"{report.readable_pages} of {report.total_pages}"),
        ("Evidence for the thesis", support.capitalize()),
        ("Quoted claims verified", f"{grounding.verified} of {grounding.factual}"),
        ("Model", f"{model} ({input_tokens:,} in / {output_tokens:,} out tokens)"),
        ("Generated", date.today().isoformat()),
    ]
    missing = [r.category.label for r in report.missing]
    warnings = report.extraction_warnings + grounding.issues
    if forced_shrink:
        warnings.append("The draft overflowed one page and was shrunk to fit.")
    over = length_issues(analysis)

    accent = _SUPPORT_COLOR.get(support, _AMBER)
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:{_GRAY}">
  <div style="max-width:620px;margin:0 auto;background:#FFFFFF;border:1px solid {_RULE};
              border-radius:10px;padding:28px 30px">
    <div style="font:600 11px/1.4 {_SANS};letter-spacing:.1em;text-transform:uppercase;color:{_CORAL};
                margin-bottom:8px">Competition analysis &middot; seed-stage memo</div>
    <div style="font:800 22px/1.25 {_SANS};color:{_INK}">{escape(company)}</div>
    <div style="font:400 14px/1.5 {_SANS};color:{_MUTED};margin:2px 0 16px">{escape(analysis.one_line)}</div>
    <div style="width:40px;height:4px;background:{_CORAL};margin-bottom:8px"></div>
    <div style="font:700 16px/1.4 {_SANS};color:{_INK};margin-bottom:14px">{escape(analysis.headline)}</div>
    <div style="background:{_CREAM};border-left:3px solid {accent};padding:10px 12px;
                font:400 13px/1.5 {_SANS};color:{_BODY};margin-bottom:18px">
      <b style="color:{_INK}">Evidence for the thesis: {escape(support.capitalize())}.</b>
      {escape(analysis.thesis_support_rationale)}
    </div>
    <table style="border-collapse:collapse;width:100%">{_rows(facts)}</table>
    {_bullets("What the materials do not show", analysis.evidence_gaps, _MUTED)}
    {_bullets("Questions for the founder", analysis.diligence_questions, _MUTED)}
    {_bullets("Evidence areas missing before generation", missing, _MUTED)}
    {_bullets("Warnings", warnings, _CORAL)}
    {_bullets("Over template length limits", over, _AMBER)}
    <div style="margin-top:22px;padding-top:14px;border-top:1px solid {_RULE};
                font:400 12px/1.6 {_SANS};color:{_MUTED}">
      Attached: the one-page PDF as generated (before any edits in the app), the draft as JSON,
      and the evidence check. The analysis is based solely on the uploaded materials and has not
      been independently verified.
    </div>
  </div>
</body></html>"""

    lines = [f"Competition analysis generated: {company}", "", analysis.headline, "",
             f"Evidence for the thesis: {support.capitalize()}. {analysis.thesis_support_rationale}", ""]
    lines += [f"{k}: {v}" for k, v in facts]
    for title, items in (("What the materials do not show", analysis.evidence_gaps),
                         ("Questions for the founder", analysis.diligence_questions),
                         ("Evidence areas missing before generation", missing),
                         ("Warnings", warnings), ("Over template length limits", over)):
        if items:
            lines += ["", f"{title}:"] + [f"  - {i}" for i in items[:10]]
    lines += ["", "Attached: the one-page PDF as generated, the draft as JSON, and the evidence check. "
                  "Based solely on the uploaded materials; not independently verified."]
    return subject, html, "\n".join(lines)


def build_attachments(analysis: Analysis, grounding: GroundingReport, pdf: bytes) -> list[Attachment]:
    name = safe_name(analysis.company_name)
    evidence = {
        "grounding_note": grounding.note(),
        "issues": grounding.issues,
        "claims": [{"claim": c.statement, "refs": c.refs, "quote": c.quote, "check": k.status}
                   for c, k in zip(analysis.claims, grounding.claim_checks)],
    }
    candidates = [
        Attachment(f"{name}_Competition_Analysis.pdf", pdf),
        Attachment(f"{name}_competition_draft.json", analysis.model_dump_json(indent=2).encode("utf-8")),
        Attachment(f"{name}_evidence.json", json.dumps(evidence, indent=2).encode("utf-8")),
    ]
    kept, total = [], 0
    for a in candidates:
        if total + len(a.content) <= MAX_TOTAL_ATTACHMENT_BYTES:
            kept.append(a)
            total += len(a.content)
    return kept


def _running_under_pytest() -> bool:
    """A function so tests can patch it to exercise the real dispatch path."""
    return "PYTEST_CURRENT_TEST" in os.environ


def send_result_email(analysis: Analysis, grounding: GroundingReport, bundle: SourceBundle,
                      report: EvidenceReport, pdf: bytes, *, model: str, input_tokens: int,
                      output_tokens: int, scale: float = 1.0, forced_shrink: bool = False) -> EmailOutcome:
    """Email one generated analysis. Never raises."""
    recipients = config.notify_recipients()
    if _running_under_pytest():
        return EmailOutcome(False, "suppressed under pytest", recipients)
    if not config.notifications_enabled():
        reason = "RESEND_API_KEY is not set" if not config.RESEND_API_KEY else "email notifications are off"
        return EmailOutcome(False, reason, recipients)
    try:
        subject, html, text = build_message(analysis, grounding, bundle, report, model=model,
                                            input_tokens=input_tokens, output_tokens=output_tokens,
                                            scale=scale, forced_shrink=forced_shrink)
        message_id = ResendMailer().send(sender=config.NOTIFY_EMAIL_FROM, to=recipients, subject=subject,
                                         html=html, text=text,
                                         attachments=build_attachments(analysis, grounding, pdf))
        return EmailOutcome(True, message_id or "sent", recipients)
    except ResendError as exc:
        return EmailOutcome(False, str(exc), recipients)
    except Exception as exc:  # a mail failure must never cost the user their analysis
        return EmailOutcome(False, f"unexpected {type(exc).__name__}", recipients)
