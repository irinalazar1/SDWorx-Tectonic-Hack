"""Optional Gemini (Vertex AI) integration.

Used when DETECTION_ENGINE=gemini. It re-extracts claims from each knowledge item
and judges whether two claims contradict. Any failure falls back to the rules
engine, so the demo never breaks because of the network or quota.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from . import config

log = logging.getLogger("faultlines.llm")


@lru_cache(maxsize=1)
def _client():
    from google import genai  # imported lazily: optional dependency

    if not config.GCP_PROJECT:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
    return genai.Client(vertexai=True, project=config.GCP_PROJECT, location=config.GCP_LOCATION)


def _generate_json(prompt: str) -> object:
    from google.genai import types

    response = _client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    return json.loads(response.text)


def available() -> bool:
    if config.DETECTION_ENGINE != "gemini":
        return False
    try:
        _client()
        return True
    except Exception as exc:  # noqa: BLE001 - any setup failure means "not available"
        log.warning("Gemini unavailable, using rules engine: %s", exc)
        return False


EXTRACT_PROMPT = """You extract checkable factual claims from an internal payroll policy.
Return a JSON list. Each element: {{"subject": str, "value": str, "text": str}}.
- "subject" is a short dotted key for WHAT the claim is about. Reuse one of these
  existing subjects whenever the claim is about the same thing: {subjects}
- "value" is a short normalised answer (e.g. "accrues", "does_not_accrue", "may", "92_percent").
- "text" is one plain sentence stating the claim.
Only include rules and facts, not procedures. At most 5 claims.

Policy title: {title}
Country: {country}
Policy text:
{body}
"""

JUDGE_PROMPT = """Two internal payroll policies for the same country make these claims about the same subject.
Do they contradict each other, meaning a consultant following one would give a different answer
than a consultant following the other? Return JSON: {{"contradicts": bool, "explanation": str}}.
The explanation is one sentence a payroll consultant understands.

Claim A ({title_a}): {text_a}
Claim B ({title_b}): {text_b}
"""


def extract_claims(item: dict, known_subjects: list[str]) -> list[dict] | None:
    try:
        data = _generate_json(EXTRACT_PROMPT.format(
            subjects=", ".join(sorted(set(known_subjects))) or "(none yet)",
            title=item["title"], country=item["country"], body=item["body"]))
        if isinstance(data, dict):  # models sometimes wrap the list
            data = data.get("claims", [])
        claims = [c for c in data if isinstance(c, dict) and {"subject", "value", "text"} <= c.keys()]
        if not claims:
            return None
        return [{k: str(c[k])[:300] for k in ("subject", "value", "text")} for c in claims[:5]]
    except Exception as exc:  # noqa: BLE001
        log.warning("Claim extraction failed for %s: %s", item.get("id"), exc)
        return None


def judge_conflict(a: dict, b: dict) -> tuple[bool, str] | None:
    try:
        data = _generate_json(JUDGE_PROMPT.format(
            title_a=a["title"], text_a=a["text"], title_b=b["title"], text_b=b["text"]))
        return bool(data.get("contradicts")), str(data.get("explanation", ""))[:400]
    except Exception as exc:  # noqa: BLE001
        log.warning("Conflict judgement failed: %s", exc)
        return None
