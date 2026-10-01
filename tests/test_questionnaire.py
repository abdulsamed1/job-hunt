"""Unit tests for dynamic application questionnaire resolution."""

from job_hunt.automation.browser import BrowserApplicationEngine
from job_hunt.models import CandidateProfile


def test_questionnaire_answer_resolution_defaults():
    engine = BrowserApplicationEngine()
    profile = CandidateProfile(
        full_name="Abdulsamed Hamdy",
        first_name="Abdulsamed",
        last_name="Hamdy",
        email="abdulsamedhamdy@gmail.com",
        phone="+201026046467",
        location="Cairo, Egypt",
        years_of_experience=4,
        work_authorization="Authorized to work in Egypt, Remote Worldwide",
        sponsorship_required=False,
        open_to_remote=True,
        citizenship="Egypt",
        authorized_countries=["Egypt"],
    )

    # Work Authorization (tri-split: positive match only, never blanket Yes)
    assert engine._resolve_question_answer("Are you legally authorized to work in Egypt?", profile) == "Yes"
    assert engine._resolve_question_answer("Are you legally authorized to work in this country?", profile) is None

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
        email="abdulsamedhamdy@gmail.com",
        phone="+201026046467",
        location="Cairo, Egypt",
        custom_answers={
            "Favorite programming language": "Python and Go",
            "Target salary": "$90,000 USD",
        },
    )

    assert engine._resolve_question_answer("What is your favorite programming language?", profile) == "Python and Go"
    assert engine._resolve_question_answer("Target salary expectation", profile) == "$90,000 USD"


def test_option_mapper_exact_and_whole_word():
    from job_hunt.automation.browser import match_answer_to_option

    assert match_answer_to_option("Yes", ["Yes", "No"]) == "Yes"
    assert match_answer_to_option("yes", ["YES", "NO"]) == "YES"
    assert match_answer_to_option("Python", ["python (3+ years)", "Java"]) == "python (3+ years)"


def test_option_mapper_substring_traps():
    from job_hunt.automation.browser import match_answer_to_option

    # "No" must not match "Knowledgeable"; "Yes" must not match "Yesterday"
    assert match_answer_to_option("No", ["Knowledgeable", "No"]) == "No"
    assert match_answer_to_option("Yes", ["Yesterday", "Yes"]) == "Yes"
    assert match_answer_to_option("No", ["Knowledgeable"]) is None


def test_option_mapper_negation_guard():
    from job_hunt.automation.browser import match_answer_to_option

    assert match_answer_to_option("Yes", ["No"]) is None
    assert match_answer_to_option("No", ["Yes"]) is None
    assert match_answer_to_option("Yes", ["No way", "Yes"]) == "Yes"
    assert match_answer_to_option("No", ["Nope", "Yes"]) == "Nope"
    assert match_answer_to_option("Yes", ["Maybe"]) is None


def test_option_mapper_decline_options():
    from job_hunt.automation.browser import match_answer_to_option

    out = match_answer_to_option(
        "I choose not to disclose", ["Male", "Female", "Decline to self-identify"]
    )
    assert out == "Decline to self-identify"


def test_checkbox_action_splits_attestation_from_routine():
    from job_hunt.automation.browser import checkbox_action

    assert checkbox_action("I certify the above statements are true") == "skip"
    assert checkbox_action("I attest under penalty of perjury") == "skip"
    assert checkbox_action("I authorize a background check") == "skip"
    assert checkbox_action("I consent to drug testing") == "skip"
    assert checkbox_action("I agree to the privacy policy") == "check"
    assert checkbox_action("I accept the terms of this application") == "check"
    assert checkbox_action("I consent to processing of my data") == "check"
    assert checkbox_action("Follow ExampleCorp on LinkedIn") == "skip"
    assert checkbox_action("") == "skip"


def test_submit_blocked_reason_requires_required_unanswered():
    from job_hunt.automation.browser import BrowserApplicationEngine

    engine = BrowserApplicationEngine()
    engine.last_unanswered = []
    assert engine.submit_blocked_reason() is None
    engine.last_unanswered = [("Nickname (optional)", False)]
    assert engine.submit_blocked_reason() is None
    engine.last_unanswered = [("Work authorization", True)]
    reason = engine.submit_blocked_reason()
    assert reason is not None and "Work authorization" in reason


def test_work_auth_tri_split():
    from job_hunt.automation.browser import BrowserApplicationEngine
    from job_hunt.models import CandidateProfile

    engine = BrowserApplicationEngine()
    profile = CandidateProfile(
        full_name="A B", first_name="A", last_name="B",
        email="a@b.com", phone="1", location="Cairo, Egypt",
        citizenship="Egypt", authorized_countries=["Egypt"],
        sponsorship_required=False,
    )

    # Citizenship: exact match / mismatch / unknown country
    assert engine._resolve_question_answer("Are you a citizen of Egypt?", profile) == "Yes"
    assert engine._resolve_question_answer("Are you a citizen of Germany?", profile) == "No"
    assert engine._resolve_question_answer("What is your citizenship?", profile) is None

    # Authorization: positive match only, never blanket Yes
    assert engine._resolve_question_answer("Are you legally authorized to work in Egypt?", profile) == "Yes"
    assert engine._resolve_question_answer("Are you legally authorized to work in Germany?", profile) is None
    assert engine._resolve_question_answer("Do you have the right to work in the USA?", profile) is None

    # Sponsorship unchanged
    assert engine._resolve_question_answer("Will you require sponsorship?", profile) == "No"


def test_needs_confirmation_flags_sensitive_gaps():
    from job_hunt.automation.browser import needs_confirmation

    assert needs_confirmation("Passport number") is True
    assert needs_confirmation("National ID / SSN") is True
    assert needs_confirmation("Reference name and phone") is True
    assert needs_confirmation("Current salary in USD") is True
    assert needs_confirmation("Bank IBAN for payroll") is True
    assert needs_confirmation("Nickname") is False
    assert needs_confirmation("Are you authorized to work here?") is False
    assert needs_confirmation("How did you hear about us?") is False


def test_ats_quirks_catalog_covers_known_systems():
    from job_hunt.automation.browser import ATS_QUIRKS

    for ats in ("ashby", "lever", "workable", "workday", "greenhouse"):
        assert ats in ATS_QUIRKS, ats
        rules = ATS_QUIRKS[ats]
        assert isinstance(rules, list) and len(rules) >= 1
        for rule in rules:
            assert "id" in rule and "do" in rule and "never" in rule
