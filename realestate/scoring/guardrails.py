from __future__ import annotations

import re

PROHIBITED_STEERING_PATTERNS = [
    r"\bracial composition\b",
    r"\bracial makeup\b",
    r"\brace of (?:the )?neighbou?rhood\b",
    r"\bmajority white\b",
    r"\bminority neighbou?rhood\b",
    r"\breligious community\b",
    r"\bnational origin\b",
    r"\bethnic makeup\b",
    r"\bdemographic(?:s)? (?:makeup|profile|composition)\b",
    r"\bgood for families because\b",
]


def guardrail_violations(text: str) -> list[str]:
    violations = []
    for pattern in PROHIBITED_STEERING_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            violations.append(pattern)
    return violations


def assert_guardrail_safe(text: str) -> None:
    violations = guardrail_violations(text)
    if violations:
        raise ValueError(f"Report contains prohibited steering language: {violations}")
