"""Unit tests for dynamic application questionnaire resolution."""

from job_hunt.automation.browser import BrowserApplicationEngine
from job_hunt.models import CandidateProfile


def test_questionnaire_answer_resolution_defaults():
    engine = BrowserApplicationEngine()
    profile = CandidateProfile(
        full_name="Abdulsamed Hamdy",
        first_name="Abdulsamed",
        last_name="Hamdy",
        email="abdalsamed71@gmail.com",
        phone="+201026046467",
        location="Cairo, Egypt",
        years_of_experience=4,
        work_authorization="Authorized to work in Egypt, Remote Worldwide",
        sponsorship_required=False,
        open_to_remote=True,
    )

    # Work Authorization
    assert engine._resolve_question_answer("Are you legally authorized to work in this country?", profile) == "Yes"

    # Sponsorship
    assert engine._resolve_question_answer("Will you now or in the future require visa sponsorship?", profile) == "No"

    # Remote willingness
    assert engine._resolve_question_answer("Are you open to remote work?", profile) == "Yes"

    # Years of experience
    assert engine._resolve_question_answer("How many years of software engineering experience do you have?", profile) == "4"

    # Notice period
    assert engine._resolve_question_answer("What is your notice period or earliest start date?", profile) == "Immediate / 2 weeks"

    # Salary expectations
    assert engine._resolve_question_answer("What are your expected salary / compensation requirements?", profile) == "Competitive / Negotiable"

    # Demographics / Voluntary disclosure
    assert engine._resolve_question_answer("Gender / Voluntary Self-Identification", profile) == "I choose not to disclose"


def test_questionnaire_custom_answers_override():
    engine = BrowserApplicationEngine()
    profile = CandidateProfile(
        full_name="Abdulsamed Hamdy",
        first_name="Abdulsamed",
        last_name="Hamdy",
        email="abdalsamed71@gmail.com",
        phone="+201026046467",
        location="Cairo, Egypt",
        custom_answers={
            "Favorite programming language": "Python and Go",
            "Target salary": "$90,000 USD",
        },
    )

    assert engine._resolve_question_answer("What is your favorite programming language?", profile) == "Python and Go"
    assert engine._resolve_question_answer("Target salary expectation", profile) == "$90,000 USD"
