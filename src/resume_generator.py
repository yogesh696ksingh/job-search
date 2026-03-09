"""
src/resume_generator.py
=======================
Generates a tailored résumé PDF for a specific job posting.

Workflow
--------
1. Load the master résumé YAML (``data/master_resume.yaml``).
2. Ask GPT to select and rewrite the most relevant experience bullets and
   project highlights for the target job.
3. Write a patched RenderCV YAML to a temporary file.
4. Call ``rendercv render`` to produce the PDF.
5. Return the path to the generated PDF.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_RESUME_PATH = REPO_ROOT / "data" / "master_resume.yaml"
OUTPUT_DIR = REPO_ROOT / "output"


def _get_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)


# ──────────────────────────────────────────────────────────────────────────────
# GPT: select + rewrite bullets
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert technical résumé writer.
Given a job description and a candidate's master résumé (as JSON), return a
JSON object with the following structure:

{
  "summary": "One tailored summary sentence (≤ 3 lines).",
  "experience": [
    {
      "company":    "...",
      "position":   "...",
      "highlights": ["bullet 1", "bullet 2", ...]
    },
    ...
  ],
  "projects": [
    { "name": "...", "highlights": ["..."] },
    ...
  ],
  "ats_keywords": ["keyword1", "keyword2"]
}

Rules:
- Keep ONLY the most relevant experience entries (max 3 roles).
- For each kept role select 2-4 bullets; rewrite them to emphasise keywords
  from the JD while staying factually accurate.
- Include 1-2 relevant projects.
- ats_keywords: list up to 10 important keywords from the JD that should
  appear in the résumé but do not yet.
- Return ONLY the JSON object, no markdown or extra text.
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _tailor_resume_content(
    master: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    client = _get_client()
    master_json = json.dumps(master.get("cv", master), ensure_ascii=False)
    jd_snippet = (job.get("description", "") or "")[:2000]

    user_prompt = (
        f"## Job Posting\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Description:\n{jd_snippet}\n\n"
        f"## Master Résumé (JSON)\n{master_json}"
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=2500,
    )
    content = response.choices[0].message.content.strip()
    return json.loads(content)


# ──────────────────────────────────────────────────────────────────────────────
# Patch master résumé YAML with tailored content
# ──────────────────────────────────────────────────────────────────────────────

def _patch_resume(master: dict[str, Any], tailored: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copy of *master* patched with GPT-tailored content."""
    patched = copy.deepcopy(master)
    cv = patched.setdefault("cv", {})
    sections = cv.setdefault("sections", {})

    # Summary
    if tailored.get("summary"):
        sections["summary"] = [tailored["summary"]]

    # Experience
    if tailored.get("experience"):
        new_exp = []
        master_exp_by_company: dict[str, dict] = {
            e.get("company", ""): e
            for e in sections.get("experience", [])
            if isinstance(e, dict)
        }
        for t_entry in tailored["experience"]:
            base = copy.deepcopy(
                master_exp_by_company.get(t_entry.get("company", ""), t_entry)
            )
            base["highlights"] = t_entry.get("highlights", base.get("highlights", []))
            new_exp.append(base)
        sections["experience"] = new_exp

    # Projects
    if tailored.get("projects"):
        sections["projects"] = tailored["projects"]

    return patched


# ──────────────────────────────────────────────────────────────────────────────
# RenderCV PDF generation
# ──────────────────────────────────────────────────────────────────────────────

def _render_pdf(resume_yaml: dict[str, Any], output_path: Path) -> Path:
    """
    Dump *resume_yaml* to a temp file and call ``rendercv render``.

    Returns the path to the generated PDF.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        yaml.dump(resume_yaml, tmp, allow_unicode=True, sort_keys=False)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["rendercv", "render", tmp_path, "--output-folder-name", str(output_path.parent)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error("rendercv stderr:\n%s", result.stderr)
            raise RuntimeError(f"rendercv failed (exit {result.returncode}): {result.stderr[:500]}")
        logger.info("rendercv stdout:\n%s", result.stdout[:200])
    finally:
        os.unlink(tmp_path)

    # RenderCV puts the PDF inside output_path.parent / <name>_CV.pdf
    candidate_name = (
        resume_yaml.get("cv", {}).get("name", "Resume")
        .replace(" ", "_")
    )
    generated_pdf = output_path.parent / f"{candidate_name}_CV.pdf"
    if generated_pdf.exists():
        generated_pdf.rename(output_path)

    return output_path


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def generate_tailored_resume(
    job: dict[str, Any],
    master_resume_path: Path = MASTER_RESUME_PATH,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, list[str]]:
    """
    Generate a PDF résumé tailored to *job*.

    Returns
    -------
    (pdf_path, ats_keywords)
        *pdf_path*      — path to the generated PDF.
        *ats_keywords*  — keywords GPT flagged as missing.
    """
    with open(master_resume_path, encoding="utf-8") as f:
        master = yaml.safe_load(f)

    logger.info(
        "Tailoring résumé for: %s @ %s", job.get("title"), job.get("company")
    )
    tailored = _tailor_resume_content(master, job)
    ats_keywords: list[str] = tailored.get("ats_keywords", [])

    patched = _patch_resume(master, tailored)

    slug = _job_slug(job)
    pdf_path = output_dir / f"resume_{slug}.pdf"

    _render_pdf(patched, pdf_path)
    logger.info("Résumé PDF written to %s", pdf_path)
    return pdf_path, ats_keywords


def _job_slug(job: dict[str, Any]) -> str:
    import re
    raw = f"{job.get('company', 'unknown')}_{job.get('title', 'role')}"
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:60]
