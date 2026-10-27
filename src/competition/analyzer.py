"""Structured Claude calls: sources in, validated Analysis out, held to the template's limits."""

from __future__ import annotations

from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel, Field, ValidationError, create_model

from . import config
from . import template as T
from .evidence import EvidenceReport, GroundingReport, ground
from .ingest import SourceBundle
from .prompts import SYSTEM_PROMPT, TIGHTEN_SYSTEM_PROMPT, build_tighten_message, build_user_message
from .schema import Analysis

# Server-side refusal fallback: a policy decline is re-run on Anthropic's
# recommended fallback model inside the same call instead of failing the memo.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Editing to a word count is a simple task; it does not need generation-level effort.
TIGHTEN_EFFORT = "medium"
TIGHTEN_ATTEMPTS = 3
# Models overshoot word targets by a few words, so ask for less than the hard
# limit: first at 85% of it, then at 70% for passages that still came back over.
TIGHTEN_TARGETS = (0.85, 0.70, 0.70)


def tighten_target(limit: int, attempt: int) -> int:
    return max(1, int(limit * TIGHTEN_TARGETS[min(attempt, len(TIGHTEN_TARGETS) - 1)]))


class AnalysisError(Exception):
    """A failure the user can act on. The message is shown in the UI."""


@dataclass
class TightenResult:
    shortened: int = 0                                   # passages rewritten shorter
    remaining: list[T.Overrun] = field(default_factory=list)  # still over after all attempts
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None                             # set when the call failed; draft unchanged


@dataclass
class AnalysisResult:
    analysis: Analysis
    grounding: GroundingReport
    model: str
    input_tokens: int
    output_tokens: int
    tighten: TightenResult | None = None


def make_client(api_key: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _call(client, *, system: str, user: str, output_format: type[BaseModel], effort: str):
    """One streamed structured call. Returns the final message; raises AnalysisError."""
    try:
        with client.beta.messages.stream(
            model=config.MODEL,
            max_tokens=config.MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            output_format=output_format,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AnalysisError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AnalysisError(f"The API key lacks access to {config.MODEL}.") from exc
    except anthropic.RateLimitError as exc:
        raise AnalysisError("Anthropic rate limit reached. Wait a minute and try again; "
                            "your uploads and draft are kept.") from exc
    except anthropic.BadRequestError as exc:
        raise AnalysisError(f"The request was rejected: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"Anthropic API error ({exc.status_code}). Try again shortly.") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError("Could not reach the Anthropic API. Check the network connection.") from exc
    except ValidationError as exc:
        raise AnalysisError("The model returned output that did not match the expected "
                            "structure. Try again.") from exc

    if message.stop_reason == "refusal":
        raise AnalysisError("The model declined to analyse these materials. Check that the upload is a pitch deck.")
    if message.stop_reason == "max_tokens":
        raise AnalysisError("The response was cut off before it finished. Try again.")
    if message.parsed_output is None:
        raise AnalysisError("The model returned no output. Try again.")
    return message


def tighten(analysis: Analysis, client: anthropic.Anthropic | None = None) -> TightenResult:
    """Rewrite passages that exceed the template's word limits, in place.

    Only over-limit passages are sent, and a rewrite is kept only when it is
    shorter than what it replaces, so this can never lengthen the draft. Never
    raises: on an API failure the draft is left as it was and the error recorded.
    """
    result = TightenResult()
    overruns = T.length_overruns(analysis)
    if not overruns:
        return result
    client = client or make_client()

    shortened_paths: set = set()
    last_attempt_words: dict = {}  # path -> word count of the model's previous rewrite

    for attempt in range(TIGHTEN_ATTEMPTS):
        passages = [(f"p{i}", o, T.get_text(analysis, o.path), tighten_target(o.limit, attempt),
                     last_attempt_words.get(o.path)) for i, o in enumerate(overruns)]
        Rewrites = create_model("Rewrites", **{
            pid: (str, Field(description=f"Rewrite of passage {pid} in at most {target} words."))
            for pid, _, _, target, _ in passages})
        try:
            message = _call(client, system=TIGHTEN_SYSTEM_PROMPT, user=build_tighten_message(passages),
                            output_format=Rewrites, effort=TIGHTEN_EFFORT)
        except AnalysisError as exc:
            result.error = str(exc)
            break
        result.input_tokens += message.usage.input_tokens
        result.output_tokens += message.usage.output_tokens

        for pid, overrun, original, _, _ in passages:
            rewrite = " ".join(str(getattr(message.parsed_output, pid, "") or "").split())
            if not rewrite:
                continue
            last_attempt_words[overrun.path] = T.word_count(rewrite)
            if T.word_count(rewrite) < T.word_count(original):
                T.set_text(analysis, overrun.path, rewrite)
                shortened_paths.add(overrun.path)
        result.shortened = len(shortened_paths)
        overruns = T.length_overruns(analysis)
        if not overruns:
            break

    result.remaining = overruns
    return result


def generate(bundle: SourceBundle, company_name: str, report: EvidenceReport,
             client: anthropic.Anthropic | None = None) -> AnalysisResult:
    if not bundle.readable_refs():
        raise AnalysisError("None of the uploaded files contain extractable text, so there is nothing "
                            "to analyse. Upload a text-based PDF or the original PPTX.")
    if bundle.total_chars() > config.MAX_SOURCE_CHARS:
        raise AnalysisError(
            f"The uploaded materials contain about {bundle.total_chars():,} characters, more than the "
            f"{config.MAX_SOURCE_CHARS:,} this tool sends in one request. Remove the largest supporting "
            "files and try again; nothing is truncated silently."
        )

    client = client or make_client()
    message = _call(client, system=SYSTEM_PROMPT, user=build_user_message(bundle, company_name, report),
                    output_format=Analysis, effort=config.EFFORT)
    analysis = message.parsed_output

    # The user-confirmed name wins over whatever the model read off the deck.
    analysis.company_name = company_name.strip() or analysis.company_name
    grounding = ground(analysis, bundle)
    # Prose length does not affect grounding: refs and quotes live outside the passages rewritten.
    tightened = tighten(analysis, client)
    usage = message.usage
    return AnalysisResult(analysis, grounding, getattr(message, "model", config.MODEL),
                          usage.input_tokens + tightened.input_tokens,
                          usage.output_tokens + tightened.output_tokens, tightened)
