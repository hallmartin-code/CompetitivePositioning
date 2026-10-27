"""One structured Claude call: sources in, validated Analysis out."""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from pydantic import ValidationError

from . import config
from .evidence import EvidenceReport, GroundingReport, ground
from .ingest import SourceBundle
from .prompts import SYSTEM_PROMPT, build_user_message
from .schema import Analysis

# Server-side refusal fallback: a policy decline is re-run on Anthropic's
# recommended fallback model inside the same call instead of failing the memo.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnalysisError(Exception):
    """A failure the user can act on. The message is shown in the UI."""


@dataclass
class AnalysisResult:
    analysis: Analysis
    grounding: GroundingReport
    model: str
    input_tokens: int
    output_tokens: int


def make_client(api_key: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


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
    try:
        with client.beta.messages.stream(
            model=config.MODEL,
            max_tokens=config.MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(bundle, company_name, report)}],
            thinking={"type": "adaptive"},
            output_config={"effort": config.EFFORT},
            output_format=Analysis,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise AnalysisError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AnalysisError(f"The API key lacks access to {config.MODEL}.") from exc
    except anthropic.RateLimitError as exc:
        raise AnalysisError("Anthropic rate limit reached. Wait a minute and generate again; "
                            "your uploads and draft are kept.") from exc
    except anthropic.BadRequestError as exc:
        raise AnalysisError(f"The request was rejected: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"Anthropic API error ({exc.status_code}). Try again shortly.") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError("Could not reach the Anthropic API. Check the network connection.") from exc
    except ValidationError as exc:
        raise AnalysisError("The model returned an analysis that did not match the expected "
                            "structure. Generate again.") from exc

    if message.stop_reason == "refusal":
        raise AnalysisError("The model declined to analyse these materials. Check that the upload is a pitch deck.")
    if message.stop_reason == "max_tokens":
        raise AnalysisError("The response was cut off before it finished. Generate again.")

    analysis = message.parsed_output
    if analysis is None:
        raise AnalysisError("The model returned no analysis. Generate again.")

    # The user-confirmed name wins over whatever the model read off the deck.
    analysis.company_name = company_name.strip() or analysis.company_name
    grounding = ground(analysis, bundle)
    usage = message.usage
    return AnalysisResult(analysis, grounding, getattr(message, "model", config.MODEL),
                          usage.input_tokens, usage.output_tokens)
