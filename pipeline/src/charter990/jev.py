"""Jev, TypeSafe AI's decision model: one POST per school, answered with probabilities.

The school's and candidates' facts are the state; one choice question carries
the guidance, with one choice per candidate EIN plus "none". Jev returns
probabilities and no text, so the reason stored with a decision is written
from the evidence (adjudicate.rationale), never by the model.

Env: JEV_API_KEY (or TYPESAFE_API_KEY); optional JEV_API_URL or
TYPESAFE_BASE_URL (default https://api.typesafe.ai; a URL ending in
/v1/systemone works too) and TYPESAFE_DEFAULT_MODEL (default jev-latest).
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import requests

DEFAULT_BASE_URL = "https://api.typesafe.ai"
USD_PER_M_INPUT = 0.042  # list price, October 2026; output is free


def _env(*names: str) -> str:
    return next((v for v in (os.environ.get(n, "").strip() for n in names) if v), "")


def available() -> bool:
    return bool(_env("JEV_API_KEY", "TYPESAFE_API_KEY"))


def model() -> str:
    """The alias asked for. TypeSafe only offers aliases, so the version that
    answered (the response's model) is what gets recorded."""
    return _env("TYPESAFE_DEFAULT_MODEL") or "jev-latest"


def base_url() -> str:
    url = (_env("JEV_API_URL", "TYPESAFE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    for suffix in ("/v1/systemone", "/v1"):
        url = url.removesuffix(suffix)
    return url


def questions_hash(questions: dict) -> str:
    """Part of the cache key, so a change to the question or the guidance asks again."""
    return hashlib.sha256(json.dumps(questions, sort_keys=True).encode()).hexdigest()[:16]


def ask(state: dict, questions: dict, retries: int = 3, timeout: float = 60.0) -> dict:
    """{model, answers, usage}; raises on a failed request after retrying 429s and 5xx."""
    body = {"state": state, "model": model(), "questions": questions}
    headers = {"Authorization": f"Bearer {_env('JEV_API_KEY', 'TYPESAFE_API_KEY')}"}
    for attempt in range(retries + 1):
        r = requests.post(f"{base_url()}/v1/systemone", json=body, headers=headers, timeout=timeout)
        if (r.status_code == 429 or r.status_code >= 500) and attempt < retries:
            time.sleep(float(r.headers.get("retry-after") or 2 ** attempt))
            continue
        if not r.ok:
            raise RuntimeError(f"Jev HTTP {r.status_code}: {r.text[:200]}")
        out = r.json()
        return {"model": out.get("model"), "answers": out["answers"], "usage": out.get("usage") or {}}
    raise AssertionError("unreachable")


def input_tokens(usage: dict) -> int:
    return int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
