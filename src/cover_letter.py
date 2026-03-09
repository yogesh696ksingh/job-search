"""
src/cover_letter.py
===================
Generates a plain-text cover letter for a specific job posting using Azure
OpenAI, then optionally renders it to a PDF via a simple Markdown → PDF
pipeline.

Returned artefacts
------------------
* A ``.txt`` file (always)
* A ``.pdf`` file (requires ``weasyprint`` or ``pandoc`` to be installed;
  falls back gracefully if unavailable)
"""

from __future__ import annotations

import logging
import os
import subprocess
import textwrap
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Template
from openai import AzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "cover_letter_template.txt"
OUTPUT_DIR = REPO_ROOT / "output"


def _get_client() -> AzureOpenAI:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("AZURE_OPENAI_API_KEY environment variable is not set.")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        raise EnvironmentError("AZURE_OPENAI_ENDPOINT environment variable is not set.")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )


# ──────────────────────────────────────────────────────────────────────────────
# GPT: generate cover letter body paragraphs
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert career coach and cover letter writer.
Given a job description, the candidate's résumé summary, and a cover letter
template, generate the three template variables below.

Return ONLY a JSON object with these exact keys:
{
  "company_hook":        "1-2 sentences about why THIS company is exciting (mission, product, tech).",
  "why_fit_paragraph":   "2-3 sentences connecting the candidate's specific experience to this role.",
  "impact_paragraph":    "2-3 sentences with concrete metrics/achievements most relevant to this JD."
}

Rules:
- Be specific; avoid generic phrases like "I am passionate about…"
- Reference actual technologies or projects from the résumé summary.
- Keep total output ≤ 200 words.
- Return ONLY the JSON object, no markdown or extra text.
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _generate_paragraphs(
    job: dict[str, Any],
    candidate: dict[str, Any],
    resume_summary: str,
) -> dict[str, str]:
    import json

    client = _get_client()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_MINI", "gpt-4o-mini")
    jd_snippet = (job.get("description", "") or "")[:1500]

    user_prompt = (
        f"## Job Posting\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Description:\n{jd_snippet}\n\n"
        f"## Candidate Résumé Summary\n{resume_summary}"
    )

    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=400,
    )
    content = response.choices[0].message.content.strip()
    return json.loads(content)


# ──────────────────────────────────────────────────────────────────────────────
# Render template
# ──────────────────────────────────────────────────────────────────────────────

def _render_cover_letter(
    paragraphs: dict[str, str],
    job: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    """Fill the Jinja2 cover-letter template and return the final text."""
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template_src = f.read()

    template = Template(template_src)
    return template.render(
        candidate_name=candidate.get("name", ""),
        candidate_email=candidate.get("email", ""),
        candidate_phone=candidate.get("phone", ""),
        candidate_location=candidate.get("location", ""),
        candidate_linkedin=candidate.get("linkedin", ""),
        date=date.today().strftime("%B %d, %Y"),
        company_name=job.get("company", ""),
        job_title=job.get("title", ""),
        **paragraphs,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Optional PDF rendering
# ──────────────────────────────────────────────────────────────────────────────

def _txt_to_pdf(txt_path: Path, pdf_path: Path) -> bool:
    """
    Convert a plain-text cover letter to PDF using pandoc (if available).
    Returns True on success, False if pandoc is not installed.
    """
    try:
        result = subprocess.run(
            ["pandoc", str(txt_path), "-o", str(pdf_path), "--pdf-engine=xelatex"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("Cover letter PDF created: %s", pdf_path)
            return True
        logger.warning("pandoc failed: %s", result.stderr[:300])
    except FileNotFoundError:
        logger.info("pandoc not found; skipping PDF generation for cover letter")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def generate_cover_letter(
    job: dict[str, Any],
    preferences: dict[str, Any],
    resume_summary: str = "",
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path | None]:
    """
    Generate a cover letter for *job*.

    Parameters
    ----------
    job:
        Job dict from the scraper/ranker.
    preferences:
        Loaded ``data/preferences.yaml`` dict.
    resume_summary:
        Short paragraph describing the candidate's background (from master résumé).
    output_dir:
        Directory to write output artefacts.

    Returns
    -------
    (txt_path, pdf_path | None)
    """
    candidate = preferences.get("candidate", {})

    if not resume_summary:
        resume_summary = (
            "Experienced software engineer with 5+ years building distributed "
            "systems, ML infrastructure, and data platforms."
        )

    logger.info(
        "Generating cover letter for: %s @ %s", job.get("title"), job.get("company")
    )

    paragraphs = _generate_paragraphs(job, candidate, resume_summary)
    text = _render_cover_letter(paragraphs, job, candidate)

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = _job_slug(job)

    txt_path = output_dir / f"cover_letter_{slug}.txt"
    txt_path.write_text(text, encoding="utf-8")
    logger.info("Cover letter TXT written to %s", txt_path)

    pdf_path: Path | None = None
    pdf_candidate = output_dir / f"cover_letter_{slug}.pdf"
    if _txt_to_pdf(txt_path, pdf_candidate):
        pdf_path = pdf_candidate

    return txt_path, pdf_path


def _job_slug(job: dict[str, Any]) -> str:
    import re
    raw = f"{job.get('company', 'unknown')}_{job.get('title', 'role')}"
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:60]
