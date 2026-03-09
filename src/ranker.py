"""
src/ranker.py
=============
Ranks job postings against user preferences using the Azure OpenAI API.

Each job is scored 0 – 10 and given a short reasoning string.

Returned augmented job dict includes two extra keys::

    "score":      float  # 0.0 – 10.0
    "reasoning":  str    # one-sentence explanation
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import AzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("AZURE_OPENAI_API_KEY environment variable is not set.")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise EnvironmentError("AZURE_OPENAI_ENDPOINT environment variable is not set.")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        _client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )
    return _client


# ──────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a career-coach AI that evaluates job postings for a software engineer.
Given a job description and the engineer's preferences, return a JSON object
with exactly two keys:
  "score"     – float between 0 and 10 (10 = perfect match)
  "reasoning" – one concise sentence explaining the score

Return ONLY the JSON object, no markdown, no extra text.
"""


def _build_user_prompt(job: dict[str, Any], preferences: dict[str, Any]) -> str:
    prefs_summary = _summarise_preferences(preferences)
    description_snippet = (job.get("description", "") or "")[:1500]
    return (
        f"## Job Posting\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Description:\n{description_snippet}\n\n"
        f"## Candidate Preferences\n{prefs_summary}"
    )


def _summarise_preferences(preferences: dict[str, Any]) -> str:
    lines: list[str] = []
    target_roles = preferences.get("target_roles", [])
    if target_roles:
        lines.append(f"Target roles: {', '.join(target_roles)}")

    seniority = preferences.get("seniority", [])
    if seniority:
        lines.append(f"Seniority: {', '.join(seniority)}")

    stack = preferences.get("preferred_stack", [])
    if stack:
        lines.append(f"Preferred tech stack: {', '.join(stack)}")

    bonus = preferences.get("bonus_keywords", [])
    if bonus:
        lines.append(f"Bonus keywords: {', '.join(bonus)}")

    disq = preferences.get("disqualifying_keywords", [])
    if disq:
        lines.append(f"Disqualifying keywords: {', '.join(disq)}")

    locations = preferences.get("locations", {})
    remote_ok = locations.get("remote_ok", True)
    preferred_locs = locations.get("preferred", [])
    lines.append(f"Remote OK: {remote_ok}. Preferred locations: {', '.join(preferred_locs)}")

    visa = preferences.get("visa", {})
    if visa.get("requires_sponsorship"):
        lines.append("Requires visa sponsorship: yes")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _score_job(job: dict[str, Any], preferences: dict[str, Any]) -> tuple[float, str]:
    """Call GPT and return (score, reasoning). Retries on transient failures."""
    client = _get_client()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_MINI", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(job, preferences)},
        ],
        temperature=0.2,
        max_tokens=150,
    )
    content = response.choices[0].message.content.strip()
    data = json.loads(content)
    score = float(data["score"])
    reasoning = str(data["reasoning"])
    return score, reasoning


def _pre_filter(job: dict[str, Any], preferences: dict[str, Any]) -> bool:
    """Return False if the job matches a disqualifying keyword (fast pre-filter)."""
    disq = [kw.lower() for kw in preferences.get("disqualifying_keywords", [])]
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    return not any(kw in text for kw in disq)


def rank_jobs(
    jobs: list[dict[str, Any]],
    preferences: dict[str, Any],
    min_score: float = 6.0,
) -> list[dict[str, Any]]:
    """
    Score each job with GPT and return a sorted list (highest score first).

    Jobs that fail the pre-filter or score below *min_score* are excluded.

    Parameters
    ----------
    jobs:
        Raw job dicts from the scraper.
    preferences:
        Loaded ``data/preferences.yaml`` dict.
    min_score:
        Jobs scoring below this threshold are dropped.

    Returns
    -------
    list[dict]
        Each dict is the original job dict augmented with ``score`` and
        ``reasoning`` keys, sorted descending by score.
    """
    logger.info("Ranking %d jobs with GPT …", len(jobs))
    scored: list[dict[str, Any]] = []

    for job in jobs:
        # Fast pre-filter — no API call needed
        if not _pre_filter(job, preferences):
            logger.debug("Pre-filtered out: %s @ %s", job.get("title"), job.get("company"))
            continue

        try:
            score, reasoning = _score_job(job, preferences)
        except Exception as exc:
            logger.warning("Scoring failed for %s: %s — skipping", job.get("id"), exc)
            continue

        job = {**job, "score": score, "reasoning": reasoning}
        logger.debug("%s @ %s → %.1f (%s)", job["title"], job["company"], score, reasoning)

        if score >= min_score:
            scored.append(job)

    scored.sort(key=lambda j: j["score"], reverse=True)
    logger.info("Ranked jobs above threshold: %d", len(scored))
    return scored


def classify_role(job: dict[str, Any]) -> str:
    """
    Return a human-readable role category string for a job (e.g.
    "Backend-heavy / Cloud", "ML Infra / Senior").

    Uses a lightweight GPT call; returns "Unknown" on failure.
    """
    prompt = (
        "Classify the following job posting into one of these categories:\n"
        "Backend, Frontend, Full-Stack, ML/AI, Data Platform, ML Infra, "
        "DevOps/SRE, Security, Mobile, Other.\n"
        "Also note seniority: Intern, Junior (0-2 yrs), Mid (3-5 yrs), "
        "Senior (5+ yrs), Staff/Principal, Unknown.\n\n"
        f"Title: {job.get('title', '')}\n"
        f"Description snippet: {(job.get('description', '') or '')[:800]}\n\n"
        "Return ONLY a compact string like: 'Backend / Senior'"
    )
    try:
        client = _get_client()
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_MINI", "gpt-4o-mini")
        response = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=30,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("classify_role failed: %s", exc)
        return "Unknown"
