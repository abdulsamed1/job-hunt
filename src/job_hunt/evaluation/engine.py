"""Deterministic pre-filtering and structured AI/heuristic evaluation engine."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

from job_hunt.llm.client import FreeLLMClient
from job_hunt.models import CandidateProfile, EvaluationResult, JobPosting

logger = logging.getLogger(__name__)

# IC allow list: seniority that marks individual-contributor work. It stands in
# for the positive SWE patterns below, never for the reject families:
# "Senior Sales Engineer" is a sales role and "Senior Engineering Manager" is a
# manager role, whatever the seniority marker says.
IC_ALLOW_PATTERNS = [
    re.compile(r"\blead\s+engineer\b", re.IGNORECASE),
    re.compile(r"\blead\s+software\s+engineer\b", re.IGNORECASE),
    re.compile(r"\b(?:staff|principal|senior|sr\.?)\b.*\b(?:engineer|engineers|engineering)\b", re.IGNORECASE),
]

# Build roles: the words that name the job of building software. One
# vocabulary for the whole module — the domain-word exemption below and the
# positive software check both read it, so a title that names a build role is
# treated the same way everywhere. The Worker keeps the same list
# (worker/src/lib/roles.ts).
BUILD_ROLE_WORDS = (
    "engineer",
    "engineers",
    "engineering",
    "developer",
    "developers",
    "devops",
    "architect",
    "programmer",
    "sre",
)
BUILD_ROLE_PATTERN = re.compile(
    r"\b(?:" + "|".join(BUILD_ROLE_WORDS) + r")\b", re.IGNORECASE
)


def names_build_role(title: str) -> bool:
    """True when the title names an engineering build role.

    A build role (engineer, developer, devops, architect, programmer, sre)
    makes the title a software role, and it also stops a domain word from
    rejecting it: "Full Stack Engineer - Finance" and "AI Engineer, Legal &
    Compliance" are software jobs, "Senior Accountant" and "Legal Counsel"
    are not.
    """
    return BUILD_ROLE_PATTERN.search(title or "") is not None


class RoleRejectRule(NamedTuple):
    """One reject rule: the family it belongs to, its slug and its pattern.

    The family and slug mirror worker/src/lib/roles.ts, so both runtimes name
    a rejected title the same way ("revenue:sales") and the backfill script can
    reuse this table instead of carrying a third copy of the rules.
    """

    family: str
    slug: str
    #: Canonical matched word. Its length breaks specificity ties, exactly like
    #: the Worker's "longest matched word wins".
    word: str
    pattern: "re.Pattern[str]"
    #: A domain word names a field of work, not a build role: the rule fires
    #: only when the title names no engineering build role.
    domain_only: bool = False


# Reject families, in the Worker's order. Negative role patterns (non-engineering
# or out-of-scope roles), grouped by family to mirror worker/src/lib/roles.ts
# (people-management, revenue, non-engineering).
ROLE_REJECT_RULES: List[RoleRejectRule] = [
    # people management
    RoleRejectRule("people-management", "manager", "manager", re.compile(r"\bmanager\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "team lead", "team lead", re.compile(r"\bteam\s+lead\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "group lead", "group lead", re.compile(r"\bgroup\s+lead\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "head of", "head of", re.compile(r"\bhead\s+of\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "director", "director", re.compile(r"\bdirector\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "vp", "vp", re.compile(r"\bvp\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "chief", "chief", re.compile(r"\bchief\b", re.IGNORECASE)),
    RoleRejectRule("people-management", "supervisor", "supervisor", re.compile(r"\bsupervisor\b", re.IGNORECASE)),
    # revenue
    RoleRejectRule("revenue", "sales", "sales", re.compile(r"\bsales\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "sales development", "sales development", re.compile(r"\bsales\s+development\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "business development", "business development", re.compile(r"\bbusiness\s+development\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "account executive", "account executive", re.compile(r"\baccount\s+executive\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "account manager", "account manager", re.compile(r"\baccount\s+manager\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "marketing", "marketing", re.compile(r"\bmarketing\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "partner", "partner", re.compile(r"\bpartner\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "customer success", "customer success", re.compile(r"\bcustomer\s+success\b", re.IGNORECASE)),
    RoleRejectRule("revenue", "customer support", "customer support", re.compile(r"\bcustomer\s+support\b", re.IGNORECASE)),
    # non-engineering
    RoleRejectRule("non-engineering", "recruiter", "recruiter", re.compile(r"\brecruiter\b", re.IGNORECASE)),
    RoleRejectRule("non-engineering", "talent acquisition", "talent acquisition", re.compile(r"\btalent\s+acquisition\b", re.IGNORECASE)),
    RoleRejectRule("non-engineering", "human resources", "human resources", re.compile(r"\bhuman\s+resources\b", re.IGNORECASE)),
    # Domain words name a field of work, not a build role: they reject only
    # when the title names no engineering build role ("Full Stack Engineer -
    # Finance" is a software job, "Senior Accountant" is not).
    RoleRejectRule("non-engineering", "accountant", "accountant", re.compile(r"\baccountant\b", re.IGNORECASE), domain_only=True),
    RoleRejectRule("non-engineering", "finance", "finance", re.compile(r"\bfinance\b", re.IGNORECASE), domain_only=True),
    RoleRejectRule("non-engineering", "legal", "legal", re.compile(r"\blegal\b", re.IGNORECASE), domain_only=True),
    RoleRejectRule("non-engineering", "medical", "medical", re.compile(r"\b(?:nurse|medical)\b", re.IGNORECASE), domain_only=True),
    RoleRejectRule("non-engineering", "driver", "driver", re.compile(r"\bdriver\b", re.IGNORECASE)),
    RoleRejectRule("non-engineering", "content writer", "content writer", re.compile(r"\bcontent\s+writer\b", re.IGNORECASE)),
    RoleRejectRule("non-engineering", "graphic designer", "graphic designer", re.compile(r"\bgraphic\s+designer\b", re.IGNORECASE)),
    # "intern" and "internship" are the same thing, and both are out of scope:
    # the Worker rejects both too (see worker/src/lib/roles.ts).
    RoleRejectRule("non-engineering", "intern", "intern", re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)),
    RoleRejectRule("non-engineering", "product", "product manager", re.compile(r"\bproduct\s+manager\b", re.IGNORECASE)),
]

# Negative patterns: non-engineering or out-of-scope roles. They live in
# ROLE_REJECT_RULES above, grouped by family to mirror worker/src/lib/roles.ts
# (people-management, revenue, non-engineering), so the backfill script can
# reuse the same table instead of copying it.
# The domain-word rules (accountant, finance, legal, medical) are conditional:
# they only fire when the title names no engineering build role, so they must
# be applied through classify_role_reject, not through this flat list.
NEGATIVE_ROLE_PATTERNS = [rule.pattern for rule in ROLE_REJECT_RULES]

# CRM implementation roles: Salesforce plus one companion word.
CRM_ROLE_RULE = RoleRejectRule(
    "crm", "salesforce", "salesforce", re.compile(r"\bsalesforce\b", re.IGNORECASE)
)
CRM_ROLE_COMPANIONS = ("consultant", "architect", "administrator", "analyst", "developer")


def classify_role_reject(title: str, reject_crm: bool = True) -> Optional[str]:
    """The reject rule a title matches ("revenue:sales"), or None.

    Most specific match wins: the longest matched word, first listed on a tie
    — the same rule the Worker applies (worker/src/lib/roles.ts). The reject
    families always win over the IC allow-list: a senior sales engineer is a
    sales role. A domain word (accountant, finance, legal, medical) is the one
    exception: it fires only when the title names no engineering build role.
    """
    best: Optional[RoleRejectRule] = None
    build_role = names_build_role(title)
    for rule in ROLE_REJECT_RULES:
        if rule.domain_only and build_role:
            continue
        if rule.pattern.search(title):
            if best is None or len(rule.word) > len(best.word):
                best = rule
    if reject_crm and CRM_ROLE_RULE.pattern.search(title):
        if any(re.search(rf"\b{c}\b", title, re.IGNORECASE) for c in CRM_ROLE_COMPANIONS):
            if best is None or len(CRM_ROLE_RULE.word) > len(best.word):
                best = CRM_ROLE_RULE
    return f"{best.family}:{best.slug}" if best is not None else None

# Positive role patterns for Software Engineer & related roles
SWE_ROLE_PATTERNS = [
    re.compile(r"\bsoftware\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bbackend\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bfrontend\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bfull[\s-]*stack\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bsystems?\s+engineer\b", re.IGNORECASE),
    re.compile(r"\binfrastructure\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bplatform\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bsite\s+reliability\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bsite\s+reliability\b", re.IGNORECASE),
    re.compile(r"\bsre\b", re.IGNORECASE),
    re.compile(r"\bdistributed\s+systems\b", re.IGNORECASE),
    re.compile(r"\bforward\s+deployed\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bcloud\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bdevops\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bml\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bmachine\s+learning\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bmachine\s+learning\b", re.IGNORECASE),
    re.compile(r"\bdata\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bpython\s+(?:engineer|developer)\b", re.IGNORECASE),
    re.compile(r"\bsoftware\b", re.IGNORECASE),
    re.compile(r"\bbackend\b", re.IGNORECASE),
    re.compile(r"\bfrontend\b", re.IGNORECASE),
    re.compile(r"\bfront\s+end\b", re.IGNORECASE),
    re.compile(r"\bfull[\s-]*stack\b", re.IGNORECASE),
    re.compile(r"\bdevops\b", re.IGNORECASE),
    re.compile(r"\binfrastructure\b", re.IGNORECASE),
    re.compile(r"\bmobile\b", re.IGNORECASE),
    re.compile(r"\bios\b", re.IGNORECASE),
    re.compile(r"\bandroid\b", re.IGNORECASE),
    re.compile(r"\bqa\b", re.IGNORECASE),
    re.compile(r"\bsdet\b", re.IGNORECASE),
    re.compile(r"\btest\s+engineer\b", re.IGNORECASE),
    re.compile(r"\bembedded\b", re.IGNORECASE),
    re.compile(r"\bfirmware\b", re.IGNORECASE),
    re.compile(r"\b(?:nodejs|node\.js|typescript|react|reactjs|javascript|python|golang|rust|java)\b.*\b(?:engineer|developer|engineering)\b", re.IGNORECASE),
    re.compile(r"\b(?:engineer|developer|engineering)\b.*\b(?:nodejs|node\.js|typescript|react|reactjs|javascript|python|golang|rust|java)\b", re.IGNORECASE),
]

CLEARANCE_PATTERNS = [
    re.compile(r"\bactive\s+top\s+secret\b", re.IGNORECASE),
    re.compile(r"\bts/sci\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s+clearance\s+required\b", re.IGNORECASE),
    re.compile(r"\bpolygraph\s+required\b", re.IGNORECASE),
]

# Eligibility gate: explicit citizenship / residency / clearance bars.
ELIGIBILITY_FAIL_PATTERNS = [
    re.compile(r"must be (?:a )?citizens?(?: of [^.]+)?", re.IGNORECASE),
    re.compile(r"citizenship (?:in|of) [a-z ]+ required", re.IGNORECASE),
    re.compile(r"(?:permanent\s+residen\w+|full working rights).{0,40}required", re.IGNORECASE),
    re.compile(r"only [a-z ]*citizens (?:may|can) apply", re.IGNORECASE),
    re.compile(r"security clearance", re.IGNORECASE),
]

ELIGIBILITY_PASS_PATTERNS = [
    re.compile(r"we sponsor", re.IGNORECASE),
    re.compile(r"sponsorship available", re.IGNORECASE),
    re.compile(r"international applicants welcome", re.IGNORECASE),
    re.compile(r"visa holders considered", re.IGNORECASE),
]

# Sponsorship tiers: explicit offer wins, explicit refusal blocks (when needed).
SPONSORSHIP_OFFER_PATTERNS = [
    re.compile(r"we sponsor", re.IGNORECASE),
    re.compile(r"sponsorship available", re.IGNORECASE),
    re.compile(r"visa sponsorship (?:provided|offered)", re.IGNORECASE),
    re.compile(r"will sponsor", re.IGNORECASE),
]

SPONSORSHIP_UNAVAILABLE_PATTERNS = [
    re.compile(r"no sponsorship(?: available)?", re.IGNORECASE),
    re.compile(r"without sponsorship", re.IGNORECASE),
    re.compile(r"cann?ot (?:provide|sponsor|offer).{0,30}sponsor", re.IGNORECASE),
    re.compile(r"unable to sponsor", re.IGNORECASE),
    re.compile(r"sponsorship not available", re.IGNORECASE),
]

# Disability-targeting gate: a FAIL requires (a) a `disabilit*` mention in the
# title, (b) a program noun (incl. plurals) within ±80 chars of a description
# mention, or (c) a direct disability hiring/employment phrase. Encouragement
# sentences without program/hiring nouns are suppressed; bare mentions (EEO /
# reasonable-accommodation boilerplate) still pass.
DISABILITY_PHRASE_PAT = re.compile(r"disabilit\w*", re.IGNORECASE)
PROGRAM_NOUN_PAT = re.compile(
    r"\b(programs?|initiatives?|tracks?|internships?|fellowships?|cohorts?)\b",
    re.IGNORECASE,
)
HIRING_NOUN_PAT = re.compile(
    r"\b(hiring|recruiting|recruitment|employment)\b", re.IGNORECASE
)
ENCOURAGEMENT_PAT = re.compile(r"encourag\w*|welcom|invit", re.IGNORECASE)
DISABILITY_HIRING_PHRASE_PAT = re.compile(
    r"disabilit\w*[\s-]+(?:hiring|employment|recruit(?:ment|ing))"
    r"|(?:hiring|recruit(?:ment|ing)|employment)\s+(?:(?:of|for)\s+)?"
    r"(?:(?:people|persons|individuals|candidates|applicants|employees|talent)\s+)?"
    r"with\s+disabilit\w*",
    re.IGNORECASE,
)

# Sentence breaks need punctuation followed by whitespace (or end of text), so
# abbreviations and decimals ("e.g.", "3.5") never split a sentence.
SENTENCE_BREAK_PAT = re.compile(r"[.!?](?=\s|$)|\n")

# Languages checked as explicit job-condition requirements (not programming languages).
KNOWN_LANGUAGES = [
    "english", "danish", "norwegian", "swedish", "german", "french", "spanish",
    "portuguese", "italian", "dutch", "polish", "arabic", "hindi", "chinese",
    "mandarin", "japanese", "korean", "russian", "ukrainian", "turkish", "farsi",
    "persian", "urdu", "hebrew", "greek", "czech", "slovak", "hungarian",
    "romanian", "finnish", "croatian", "serbian", "bulgarian", "thai",
    "vietnamese", "indonesian", "malay", "tagalog", "swahili", "bengali",
]

LANGUAGE_REQUIREMENT_TEMPLATES = [
    r"fluent {lang}\b",
    r"native {lang}\b",
    r"{lang} (?:fluency|required|mandatory)\b",
    r"must speak {lang}\b",
    r"communicate .{{0,40}} in {lang}\b",
    r"working language .{{0,20}}{lang}\b",
    r"{lang} as (?:the )?working language\b",
]


STRICT_ONSITE_PATTERNS = [
    re.compile(r"\bno\s+remote\b", re.IGNORECASE),
    re.compile(r"\b100%\s+on-?site\b", re.IGNORECASE),
    re.compile(r"\bstrictly\s+on-?site\b", re.IGNORECASE),
    re.compile(r"\bmust\s+work\s+from\s+(?:the\s+)?office\b", re.IGNORECASE),
    re.compile(r"\bin-?office\s+5\s+days\b", re.IGNORECASE),
]

REMOTE_KEYWORDS = [
    "remote", "anywhere", "worldwide", "global", "work from home", "wfh",
    "telecommute", "distributed", "virtual", "emea", "americas", "apac",
    "remote (us)", "remote us", "remote eu", "remote uk", "us remote",
    "eu remote", "uk remote", "flexible location", "work from anywhere"
]


def classify_tier(title: str, description: str = "") -> str:
    """Classify seniority tier: intern | entry | mid | senior | lead.

    Title keywords decide first; explicit years-of-experience requirements
    override upward only (8+ years is never entry-level).
    """
    text = f"{title or ''}".lower()
    if re.search(r"\bintern(?:ship)?\b", text):
        return "intern"
    if re.search(r"\bjunior\b|\bentry\b|\bgraduate\b|\bassociate\b", text):
        return "entry"
    if re.search(r"\bstaff\b|\bprincipal\b|\bmanager\b|\bdirector\b|\blead\b", text):
        return "lead"
    if re.search(r"\bsenior\b|\bsr\.?\b", text):
        return "senior"
    years = re.search(r"(\d+)\+?\s*years?", (description or "").lower())
    if years and int(years.group(1)) >= 8:
        return "senior"
    if years and int(years.group(1)) >= 5:
        return "senior" if re.search(r"\bsenior\b", (description or "").lower()) else "mid"
    return "mid"


def _sentence_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    """Bounds of the sentence containing [start, end).

    Breaks on punctuation followed by whitespace (or end of text) and on
    newlines, so abbreviations and decimals never split a sentence.
    """
    sent_start, sent_end = 0, len(text)
    for m in SENTENCE_BREAK_PAT.finditer(text):
        if m.end() <= start:
            sent_start = m.end()
        elif m.start() >= end:
            sent_end = m.start()
            break
    return sent_start, sent_end


def _sentence_around(text: str, start: int, end: int) -> str:
    """Return the sentence containing [start, end) without its break characters."""
    sent_start, sent_end = _sentence_bounds(text, start, end)
    return text[sent_start:sent_end]


def _clip(text: str, start: int, end: int, radius: int = 100) -> str:
    """Slice `text` around [start, end) so the matched phrase is never cut away."""
    return text[max(0, start - radius):min(len(text), end + radius)].strip()


class EvaluationEngine:
    """Evaluates job postings against a candidate profile using pre-filters, LLM, and heuristic fallback."""

    def __init__(
        self,
        min_score_threshold: float = 70.0,
        llm_client: Optional[FreeLLMClient] = None,
        use_llm: bool = True,
    ):
        self.min_score_threshold = min_score_threshold
        self.llm_client = llm_client
        self.use_llm = use_llm

    def check_eligibility_gate(self, job: JobPosting) -> Tuple[str, Optional[str]]:
        """Hard gate: explicit citizenship / residency / clearance bars.

        Returns (verdict, quote). FAIL quotes the exact posting wording and must
        stop scoring; PASS marks verified acceptance; UNVERIFIED means the posting
        is silent and evaluation proceeds.
        """
        desc = job.description or ""
        for pat in ELIGIBILITY_FAIL_PATTERNS:
            match = pat.search(desc)
            if match:
                quote = match.group(0).strip()
                # Negated or prospective contexts are not bars ("no clearance
                # required", "able to obtain clearance").
                prefix = desc[max(0, match.start() - 20):match.start()].lower()
                if re.search(r"\b(no|not|without|none)\b", prefix):
                    continue
                if re.search(r"\b(obtain|eligible for|ability to obtain)\b", prefix):
                    continue
                return "FAIL", quote
        for pat in ELIGIBILITY_PASS_PATTERNS:
            match = pat.search(desc)
            if match:
                return "PASS", match.group(0).strip()
        return "UNVERIFIED", None

    def check_language_gate(
        self, job: JobPosting, profile: CandidateProfile
    ) -> Tuple[str, Optional[str]]:
        """Hard gate: posting requires a working language the candidate never declared.

        Returns (verdict, quote). FAIL quotes the requirement and must stop scoring.
        Empty profile languages means no data to judge by -> UNVERIFIED (proceed).
        Programming languages that are also verified skills (e.g. Python) are ignored.
        """
        declared = {lang.lower() for lang in (profile.languages or [])}
        if not declared:
            return "UNVERIFIED", None
        text = f"{job.title} {job.description or ''}"
        tech_skills = {s.lower() for s in (profile.verified_skills or [])}
        required: List[str] = []
        quotes: List[str] = []
        for lang in KNOWN_LANGUAGES:
            if lang in tech_skills:
                continue
            for template in LANGUAGE_REQUIREMENT_TEMPLATES:
                match = re.search(template.format(lang=re.escape(lang)), text, re.IGNORECASE)
                if match:
                    required.append(lang)
                    quotes.append(match.group(0).strip())
                    break
        if not required:
            return "PASS", None
        missing = [lang for lang in required if lang not in declared]
        if missing:
            idx = required.index(missing[0])
            return "FAIL", quotes[idx]
        return "PASS", quotes[0]

    def _gate_rejection(
        self, job: JobPosting, gate: str, verdict: str, quote: Optional[str]
    ) -> EvaluationResult:
        reason = f"{gate} gate {verdict}: '{quote}'" if quote else f"{gate} gate {verdict}"
        return EvaluationResult(
            job_id=job.id,
            score=0.0,
            eligible=False,
            matched_skills=[],
            missing_skills=[],
            reasoning=f"Pre-filtered out: {reason}",
            pre_filtered=True,
            pre_filter_reason=reason,
        )

    def check_disability_targeting_gate(self, job: JobPosting) -> Tuple[str, Optional[str]]:
        """Hard gate: posting targets a disability-specific hiring program.

        Returns (verdict, quote). FAIL quotes the matched phrase with
        surrounding context and must stop scoring. A FAIL requires a title
        mention, a program noun (incl. plurals) within ±80 chars of a
        description mention, or a direct disability hiring/employment phrase.
        Encouragement sentences without program/hiring nouns are suppressed;
        bare mentions and generic EEO / reasonable-accommodation boilerplate
        pass.
        """
        title = job.title or ""
        desc = job.description or ""
        title_match = DISABILITY_PHRASE_PAT.search(title)
        if title_match:
            return "FAIL", _clip(title, title_match.start(), title_match.end())
        for match in DISABILITY_PHRASE_PAT.finditer(desc):
            sentence = _sentence_around(desc, match.start(), match.end())
            # Pure encouragement boilerplate ("candidates with disabilities are
            # encouraged to apply") is suppressed only while it carries no
            # program/hiring noun of its own.
            if ENCOURAGEMENT_PAT.search(sentence) and not (
                PROGRAM_NOUN_PAT.search(sentence) or HIRING_NOUN_PAT.search(sentence)
            ):
                continue
            window = desc[max(0, match.start() - 80):match.end() + 80]
            if PROGRAM_NOUN_PAT.search(window):
                return "FAIL", _clip(desc, match.start(), match.end())
        phrase = DISABILITY_HIRING_PHRASE_PAT.search(desc)
        if phrase:
            return "FAIL", _clip(desc, phrase.start(), phrase.end())
        return "PASS", None

    def check_sponsorship(
        self, job: JobPosting, profile: CandidateProfile
    ) -> Tuple[str, Optional[str]]:
        """Sponsorship tier check. Only decisive when sponsorship is required.

        Offer-wins: explicit offer -> PASS. Explicit refusal + required -> FAIL
        quoting the wording. Not required -> UNVERIFIED (irrelevant, proceed).
        Silent + required -> UNVERIFIED (proceed, unverified).
        """
        desc = job.description or ""
        for pat in SPONSORSHIP_OFFER_PATTERNS:
            match = pat.search(desc)
            if match:
                return "PASS", match.group(0).strip()
        if not profile.sponsorship_required:
            return "UNVERIFIED", None
        for pat in SPONSORSHIP_UNAVAILABLE_PATTERNS:
            match = pat.search(desc)
            if match:
                return "FAIL", match.group(0).strip()
        return "UNVERIFIED", None

    def pre_filter(self, job: JobPosting, profile: CandidateProfile) -> Tuple[bool, Optional[str]]:
        """Run fast deterministic pre-filters on title, clearance, and location."""
        title = job.title.strip()

        # Sponsorship tier gate (decisive only when sponsorship is required)
        verdict, quote = self.check_sponsorship(job, profile)
        if verdict == "FAIL":
            return False, f"Sponsorship unavailable but required: {quote}"

        # Blocked companies and keywords (preferred keywords win exceptions)
        company_lower = (job.company or "").lower()
        if any(bc.lower() in company_lower for bc in (profile.blocked_companies or []) if bc):
            return False, f"Company is blocked: {job.company}"
        desc_lower = (job.description or "").lower()
        preferred_hit = any(pk.lower() in desc_lower or pk.lower() in company_lower for pk in (profile.preferred_keywords or []) if pk)
        if not preferred_hit:
            for bk in (profile.blocked_keywords or []):
                if bk and bk.lower() in desc_lower:
                    return False, f"Blocked keyword in posting: {bk}"

        # The reject families come first, and nothing overrides them: a revenue,
        # CRM or non-engineering title is out of scope whatever its seniority
        # marker says ("Senior Sales Engineer" is a sales role), and so is a
        # people-management one ("Senior Engineering Manager" is a manager
        # role). The IC allow-list only stands in for the positive SWE check
        # below, where a seniority marker plus an engineering noun is
        # individual-contributor work even without a technology in the title.
        # A domain word (accountant, finance, legal, medical) is the one
        # exception: it fires only when the title names no engineering build
        # role ("Full Stack Engineer - Finance" is a software job).
        reject_crm = profile.reject_crm_roles
        build_role = names_build_role(title)

        # Check negative role patterns. The reason names the family and the
        # slug, so both runtimes call the same rule by the same name.
        for rule in ROLE_REJECT_RULES:
            if rule.domain_only and build_role:
                continue
            if rule.pattern.search(title):
                return False, f"Title matches negative filter: {rule.family}:{rule.slug}"

        # CRM reject (only when reject_crm is on): salesforce + a companion.
        if reject_crm and re.search(r"\bsalesforce\b", title, re.IGNORECASE):
            companions = ("consultant", "architect", "administrator", "analyst", "developer")
            if any(re.search(rf"\b{c}\b", title, re.IGNORECASE) for c in companions):
                return False, "Title matches negative filter: crm:salesforce"

        # Check positive SWE role patterns. The IC allow-list stands in for
        # them: "Staff Engineer, Platform" names no technology but is
        # individual-contributor engineering work. So does a title that names
        # an engineering build role ("AI Engineer"): a build role is a software
        # role, and a domain word never disqualifies one.
        is_ic = any(p.search(title) for p in IC_ALLOW_PATTERNS)
        matched_swe = any(pos_pat.search(title) for pos_pat in SWE_ROLE_PATTERNS)
        if not matched_swe and not is_ic and not build_role:
            return False, "Title does not match targeted Software Engineer roles"

        # Check clearance requirements in description
        desc = job.description or ""
        for cl_pat in CLEARANCE_PATTERNS:
            if cl_pat.search(desc):
                return False, f"Requires security clearance: {cl_pat.pattern}"

        # Check strict on-site requirement in foreign country when candidate is remote
        loc = (job.location or "").lower()
        cand_loc = profile.location.lower()
        cand_country = "egypt" if "egypt" in cand_loc else cand_loc.split(",")[-1].strip()

        # If candidate is in Egypt and role is outside Egypt and strictly forbids remote:
        if cand_country not in loc and not any(kw in loc for kw in ("remote", "anywhere", "worldwide", "global")):
            for onsite_pat in STRICT_ONSITE_PATTERNS:
                if onsite_pat.search(desc) or onsite_pat.search(title):
                    return False, f"Role strictly requires 100% on-site presence abroad: {onsite_pat.pattern}"

        return True, None

    def evaluate(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        threshold: Optional[float] = None,
    ) -> EvaluationResult:
        """Synchronously evaluate job against candidate profile, using LLM if available with deterministic fallback."""
        thresh = threshold if threshold is not None else self.min_score_threshold
        # 0. Hard gates run before scoring (no LLM tokens spent on gated-out roles)
        verdict, quote = self.check_disability_targeting_gate(job)
        if verdict == "FAIL":
            return self._gate_rejection(job, "Disability-targeted", verdict, quote)
        verdict, quote = self.check_eligibility_gate(job)
        if verdict == "FAIL":
            return self._gate_rejection(job, "Eligibility", verdict, quote)
        verdict, quote = self.check_language_gate(job, profile)
        if verdict == "FAIL":
            return self._gate_rejection(job, "Language", verdict, quote)
        # 1. Run pre-filter
        passed, reason = self.pre_filter(job, profile)
        if not passed:
            return EvaluationResult(
                job_id=job.id,
                score=0.0,
                eligible=False,
                matched_skills=[],
                missing_skills=[],
                reasoning=f"Pre-filtered out: {reason}",
                pre_filtered=True,
                pre_filter_reason=reason,
            )

        # 2. Try LLM evaluation if enabled and client provided
        if self.use_llm and self.llm_client:
            try:
                llm_data = self.llm_client.evaluate_job_sync(job, profile)
                if llm_data and isinstance(llm_data, dict) and "score" in llm_data:
                    res = self._build_llm_result(job.id, llm_data, threshold=thresh)
                    if res is not None:
                        return res
            except Exception as e:
                logger.warning("LLM evaluation failed, using deterministic fallback: %s", e)

        # 3. Deterministic scoring fallback
        return self._evaluate_deterministic(job, profile, threshold=thresh)

    async def evaluate_async(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        threshold: Optional[float] = None,
    ) -> EvaluationResult:
        """Asynchronously evaluate job against candidate profile, using LLM if available with deterministic fallback."""
        thresh = threshold if threshold is not None else self.min_score_threshold
        verdict, quote = self.check_disability_targeting_gate(job)
        if verdict == "FAIL":
            return self._gate_rejection(job, "Disability-targeted", verdict, quote)
        verdict, quote = self.check_eligibility_gate(job)
        if verdict == "FAIL":
            return self._gate_rejection(job, "Eligibility", verdict, quote)
        verdict, quote = self.check_language_gate(job, profile)
        if verdict == "FAIL":
            return self._gate_rejection(job, "Language", verdict, quote)
        passed, reason = self.pre_filter(job, profile)
        if not passed:
            return EvaluationResult(
                job_id=job.id,
                score=0.0,
                eligible=False,
                matched_skills=[],
                missing_skills=[],
                reasoning=f"Pre-filtered out: {reason}",
                pre_filtered=True,
                pre_filter_reason=reason,
            )

        if self.use_llm and self.llm_client:
            try:
                llm_data = await self.llm_client.evaluate_job(job, profile)
                if llm_data and isinstance(llm_data, dict) and "score" in llm_data:
                    res = self._build_llm_result(job.id, llm_data, threshold=thresh)
                    if res is not None:
                        return res
            except Exception as e:
                logger.warning("Async LLM evaluation failed, using deterministic fallback: %s", e)

        return self._evaluate_deterministic(job, profile, threshold=thresh)

    def _build_llm_result(
        self,
        job_id: Optional[int],
        data: Dict[str, Any],
        threshold: Optional[float] = None,
    ) -> Optional[EvaluationResult]:
        """Validate and transform LLM output into an EvaluationResult."""
        thresh = threshold if threshold is not None else self.min_score_threshold
        try:
            raw_score = float(data.get("score", 0.0))
            score = max(0.0, min(100.0, round(raw_score, 1)))
            is_eligible = score >= thresh

            matched = [str(s) for s in data.get("matched_skills", []) if isinstance(s, (str, int))]
            missing = [str(s) for s in data.get("missing_skills", []) if isinstance(s, (str, int))]

            reasoning_parts = []
            if data.get("reasoning"):
                reasoning_parts.append(str(data["reasoning"]))
            if data.get("seniority_fit"):
                reasoning_parts.append(f"Seniority: {data['seniority_fit']}")
            if data.get("location_fit"):
                reasoning_parts.append(f"Location: {data['location_fit']}")

            reasoning = " | ".join(reasoning_parts) or f"AI Evaluated match score: {score}/100"

            return EvaluationResult(
                job_id=job_id,
                score=score,
                eligible=is_eligible,
                matched_skills=matched,
                missing_skills=missing,
                reasoning=f"[AI-Evaluated] {reasoning}",
                pre_filtered=False,
            )
        except Exception as e:
            logger.warning("Error parsing LLM evaluation data: %s", e)
            return None

    def _evaluate_deterministic(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        threshold: Optional[float] = None,
    ) -> EvaluationResult:
        """Deterministic keyword-based evaluation fallback scoring 0-100."""
        thresh = threshold if threshold is not None else self.min_score_threshold
        text_to_scan = f"{job.title} {job.description}".lower()

        matched_skills: List[str] = []
        missing_skills: List[str] = []

        verified_skills_lower = {s.lower(): s for s in profile.verified_skills}

        for skill_lower, skill_original in verified_skills_lower.items():
            pattern = rf"\b{re.escape(skill_lower)}\b"
            if re.search(pattern, text_to_scan):
                matched_skills.append(skill_original)

        common_tech_keywords = [
            "python", "go", "golang", "rust", "java", "c++", "c", "assembly", "rtos",
            "embedded", "kernel", "typescript", "javascript", "react", "node", "django",
            "fastapi", "flask", "kubernetes", "docker", "aws", "gcp", "azure", "graphql",
            "sql", "postgresql", "mysql", "redis", "kafka", "rabbitmq", "grpc", "linux",
            "ci/cd", "terraform"
        ]

        for kw in common_tech_keywords:
            if re.search(rf"\b{re.escape(kw)}\b", text_to_scan):
                if kw not in [m.lower() for m in matched_skills]:
                    missing_skills.append(kw)

        # A) Skill match ratio (weight 55)
        if matched_skills and missing_skills:
            total = len(matched_skills) + len(missing_skills)
            skill_score = (len(matched_skills) / total) * 55.0
        elif matched_skills and not missing_skills:
            skill_score = 55.0
        elif not matched_skills and missing_skills:
            skill_score = 0.0
        else:
            skill_score = 30.0

        # B) Seniority match (weight 25)
        yoe_match = re.search(r"(\d+)\+?\s*years?", text_to_scan)
        seniority_score = 25.0
        if yoe_match:
            req_years = int(yoe_match.group(1))
            if profile.years_of_experience >= req_years:
                seniority_score = 25.0
            elif profile.years_of_experience >= req_years - 2:
                seniority_score = 12.0
            else:
                seniority_score = 0.0

        # C) Location / Remote match (weight 20)
        loc = (job.location or "").lower()
        desc_lower = (job.description or "").lower()
        title_lower = job.title.lower()

        # Check if role is remote
        is_remote = any(kw in loc or kw in title_lower for kw in REMOTE_KEYWORDS)
        if not is_remote and any(kw in desc_lower[:600] for kw in ("fully remote", "100% remote", "remote work", "remote-first", "work from anywhere")):
            is_remote = True

        if is_remote or (profile.open_to_remote and any(k in loc for k in ("anywhere", "worldwide", "global", "remote"))):
            location_score = 20.0
        elif "egypt" in loc or "cairo" in loc or "giza" in loc:
            location_score = 20.0
        elif any(country in loc for country in ("us", "united states", "uk", "europe", "germany", "canada", "netherlands", "emea")):
            # International role - candidate is open to remote worldwide
            location_score = 18.0 if profile.open_to_remote else 5.0
        elif "hybrid" in loc or "office" in loc or "in-office" in loc:
            cand_city = profile.location.split(",")[0].strip().lower()
            if cand_city and cand_city in loc:
                location_score = 20.0
            else:
                location_score = 0.0
        else:
            location_score = 15.0 if profile.open_to_remote else 10.0

        total_score = min(100.0, round(skill_score + seniority_score + location_score, 1))
        is_eligible = total_score >= thresh

        reasoning = (
            f"Score: {total_score}/100 (Skills: {skill_score:.1f}/55, Seniority: {seniority_score:.1f}/25, "
            f"Location: {location_score:.1f}/20). Matched {len(matched_skills)} verified skills: {', '.join(matched_skills[:5])}."
        )

        return EvaluationResult(
            job_id=job.id,
            score=total_score,
            eligible=is_eligible,
            matched_skills=matched_skills,
            missing_skills=missing_skills[:10],
            reasoning=f"[Deterministic] {reasoning}",
            pre_filtered=False,
        )
