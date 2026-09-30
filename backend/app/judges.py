"""Implementations of the ConflictJudge and ClaimExtractor ports.

Rules versions are deterministic and offline. Gemini versions wrap a rules fallback,
so a network or quota problem degrades gracefully instead of breaking detection.
"""
from __future__ import annotations

import logging
from typing import Protocol

from .ports import ClaimExtractor, ConflictJudge, Verdict

log = logging.getLogger("faultlines.judges")


class JsonModel(Protocol):
    def generate_json(self, prompt: str) -> object: ...


# --------------------------------------------------------------------------- rules
class RuleConflictJudge:
    """Two claims on the same subject with different normalised values contradict."""

    name = "rules"

    def judge(self, item_a: dict, claim_a: dict, item_b: dict, claim_b: dict) -> Verdict:
        differs = claim_a["value"].strip().lower() != claim_b["value"].strip().lower()
        return Verdict(contradicts=differs)


class CatalogClaimExtractor:
    """Uses the claims that come with each catalog item."""

    def extract(self, item: dict, known_subjects: list[str]) -> list[dict]:
        return list(item.get("claims", []))


# --------------------------------------------------------------------------- gemini
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


class GeminiConflictJudge:
    name = "gemini"

    def __init__(self, model: JsonModel, fallback: ConflictJudge | None = None):
        self.model = model
        self.fallback = fallback or RuleConflictJudge()

    def judge(self, item_a: dict, claim_a: dict, item_b: dict, claim_b: dict) -> Verdict:
        try:
            data = self.model.generate_json(JUDGE_PROMPT.format(
                title_a=item_a["title"], text_a=claim_a["text"], title_b=item_b["title"], text_b=claim_b["text"]))
            return Verdict(bool(data.get("contradicts")), str(data.get("explanation", ""))[:400] or None)
        except Exception as exc:  # noqa: BLE001 - any model failure falls back
            log.warning("Gemini judgement failed, using rules: %s", exc)
            return self.fallback.judge(item_a, claim_a, item_b, claim_b)


class GeminiClaimExtractor:
    def __init__(self, model: JsonModel, fallback: ClaimExtractor | None = None):
        self.model = model
        self.fallback = fallback or CatalogClaimExtractor()

    def extract(self, item: dict, known_subjects: list[str]) -> list[dict]:
        try:
            data = self.model.generate_json(EXTRACT_PROMPT.format(
                subjects=", ".join(sorted(set(known_subjects))) or "(none yet)",
                title=item["title"], country=item["country"], body=item["body"]))
            if isinstance(data, dict):  # models sometimes wrap the list
                data = data.get("claims", [])
            claims = [c for c in data if isinstance(c, dict) and {"subject", "value", "text"} <= c.keys()]
            if claims:
                return [{k: str(c[k])[:300] for k in ("subject", "value", "text")} for c in claims[:5]]
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini extraction failed for %s, using catalog claims: %s", item.get("id"), exc)
        return self.fallback.extract(item, known_subjects)
