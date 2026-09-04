"""Unit and integration tests for FreeLLMAPI integration, LLM evaluation, and zero-cost fallback."""

import json
from unittest.mock import MagicMock, patch
import pytest

from job_hunt.cv.tailor import CVTailor
from job_hunt.evaluation.engine import EvaluationEngine
from job_hunt.llm.client import FreeLLMClient
from job_hunt.models import CandidateProfile, JobPosting


@pytest.fixture
def candidate():
    return CandidateProfile(
        full_name="Alex Rivera",
        first_name="Alex",
        last_name="Rivera",
        email="alex@example.com",
        phone="+1234567890",
        location="Remote, US",
        years_of_experience=5,
        verified_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
        allowed_metrics=["40%", "10k"],
    )


@pytest.fixture
def job():
    return JobPosting(
        id=42,
        source="greenhouse",
        title="Senior Python Backend Engineer",
        company="ScaleAI",
        raw_url="https://example.com/job/42",
        canonical_url="https://example.com/job/42",
        canonical_url_hash="h42",
        role_fingerprint="rf42",
        content_hash="c42",
        location="Remote",
        description="Looking for an experienced Python developer with FastAPI and PostgreSQL expertise to build scalable microservices.",
    )


def test_free_llm_json_parsing():
    """Verify parsing of clean JSON, markdown-wrapped JSON, and DeepSeek <think> blocks."""
    # 1. Clean JSON
    raw_clean = '{"score": 88.5, "eligible": true, "matched_skills": ["Python"]}'
    parsed = FreeLLMClient._clean_and_parse_json(raw_clean)
    assert parsed is not None
    assert parsed["score"] == 88.5

    # 2. Markdown fence wrapped
    raw_md = '```json\n{"score": 75.0, "eligible": true}\n```'
    parsed_md = FreeLLMClient._clean_and_parse_json(raw_md)
    assert parsed_md is not None
    assert parsed_md["score"] == 75.0

    # 3. DeepSeek <think>...</think> reasoning block
    raw_deepseek = (
        "<think>\n"
        "Let's carefully analyze the candidate's skills against the requirements.\n"
        "Python matches, FastAPI matches.\n"
        "</think>\n"
        '```json\n{"score": 92.0, "eligible": true, "reasoning": "Excellent match"}\n```'
    )
    parsed_ds = FreeLLMClient._clean_and_parse_json(raw_deepseek)
    assert parsed_ds is not None
    assert parsed_ds["score"] == 92.0
    assert parsed_ds["reasoning"] == "Excellent match"


def test_evaluation_engine_with_llm_success(candidate, job):
    """Verify EvaluationEngine uses LLM result when available."""
    mock_client = MagicMock(spec=FreeLLMClient)
    mock_client.evaluate_job_sync.return_value = {
        "score": 87.0,
        "eligible": True,
        "matched_skills": ["Python", "FastAPI", "PostgreSQL"],
        "missing_skills": ["Kubernetes"],
        "reasoning": "Strong technical match for backend stack.",
        "seniority_fit": "Fits 5+ years requirement.",
        "location_fit": "Remote matches candidate.",
    }

    engine = EvaluationEngine(min_score_threshold=70.0, llm_client=mock_client, use_llm=True)
    res = engine.evaluate(job, candidate)

    assert res.score == 87.0
    assert res.eligible is True
    assert "[AI-Evaluated]" in res.reasoning
    assert "Python" in res.matched_skills


def test_evaluation_engine_llm_fallback(candidate, job):
    """Verify EvaluationEngine falls back to deterministic heuristic when LLM fails or times out."""
    mock_client = MagicMock(spec=FreeLLMClient)
    mock_client.evaluate_job_sync.return_value = None  # Simulates timeout/failure

    engine = EvaluationEngine(min_score_threshold=70.0, llm_client=mock_client, use_llm=True)
    res = engine.evaluate(job, candidate)

    # Should fall back to deterministic evaluation
    assert res.score > 0.0
    assert "[Deterministic]" in res.reasoning
    assert "Python" in res.matched_skills


def test_cv_tailor_with_llm_and_fact_checking(candidate, job):
    """Verify CVTailor uses LLM summary if fact check passes, but falls back if hallucinated."""
    mock_client = MagicMock(spec=FreeLLMClient)
    # Valid summary using only candidate facts (5+ years, Python, FastAPI)
    mock_client.tailor_summary_sync.return_value = (
        "Software Engineer with 5+ years of experience specializing in Python and FastAPI. "
        "Delivered high-impact distributed backend architectures."
    )

    tailor = CVTailor(llm_client=mock_client, use_llm=True)
    cv = tailor.generate_tailored_cv(job, candidate)

    assert cv.verification_passed is True
    assert "5+ years of experience" in cv.content_markdown


def test_cv_tailor_rejects_llm_hallucination(candidate, job):
    """Verify CVTailor detects and rejects LLM-invented facts and unverified metrics."""
    mock_client = MagicMock(spec=FreeLLMClient)
    # Hallucinated summary: claims 99 years of experience and invented 500% metric
    mock_client.tailor_summary_sync.return_value = (
        "Senior Architect with 99 years of experience who increased revenues by 500% at Google."
    )

    tailor = CVTailor(llm_client=mock_client, use_llm=True)
    cv = tailor.generate_tailored_cv(job, candidate)

    # Fact check should fail on the LLM summary and gracefully fall back to verified master/deterministic CV
    assert cv.verification_passed is True
    assert "99 years" not in cv.content_markdown
    assert "500%" not in cv.content_markdown
