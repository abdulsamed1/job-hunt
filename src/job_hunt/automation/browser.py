"""Browser automation engine using Playwright with stealth context, cookie bypass, frame traversal, and multi-step form handling."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from playwright.async_api import Browser, BrowserContext, Frame, Page, async_playwright

from job_hunt.automation.captcha_solver import CaptchaSolver, is_captcha_error
from job_hunt.models import ApplicationRecord, CandidateProfile, JobPosting, JobState

logger = logging.getLogger(__name__)

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    "iframe[src*='cloudflare']",
    ".g-recaptcha",
    ".h-captcha",
    "#cf-turnstile",
    "img[id*='captcha' i]",
    "img[src*='captcha' i]",
    "text=Verify you are human",
]

SUCCESS_INDICATORS = [
    "thank you for applying",
    "application submitted",
    "application received",
    "thank you for your application",
    "we have received your application",
    "thanks for applying",
    "your application was submitted",
    "your application has been submitted",
    "application complete",
    "congratulations",
    "received your submission",
]

CONSENT_ROOTS = [
    "#onetrust-banner-sdk",
    "#CybotCookiebotDialog",
    "#truste-consent-track",
    ".qc-cmp2-container",
    "#usercentrics-root",
    "[id*='cookie' i][class*='banner' i]",
    "[class*='cookie-consent' i]",
    "[aria-label*='cookie' i]",
]

CONSENT_BUTTONS = [
    "#onetrust-accept-btn-handler",
    "#onetrust-button-accept-all",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    ".qc-cmp2-button[mode='primary']",
    "#truste-consent-button",
    "button:has-text('Accept All')",
    "button:has-text('Allow All')",
    "button:has-text('Accept all cookies')",
    "button:has-text('I Agree')",
    "button:has-text('Agree')",
    "button:has-text('Got it')",
]

ATS_LINK_PATTERNS = [
    "ashbyhq.com",
    "greenhouse.io",
    "lever.co",
    "smartrecruiters.com",
    "workable.com",
    "bamboohr.com",
    "recruitee.com",
    "jobvite.com",
    "teamtailor.com",
    "myworkdayjobs.com",
    "/apply",
    "/application",
]

LINKEDIN_EASY_APPLY_SELECTORS = [
    "button:has-text('Easy Apply')",
    "button:has-text('Apply')",
    "a:has-text('Easy Apply')",
    "button:has-text('Apply now')",
    '[data-control-name="jobapply"]',
    ".jobs-apply-button",
    "button.applying-btn",
]

LINKEDIN_SKILL_TAG_SELECTORS = [
    ".jobs-search-results__skill-pill",
    '[data-control-name="jobapply-skills"]',
    ".artdeco-tag-list",
]

CLOUDFLARE_TURNSTILE_SELECTORS = [
    "iframe[src*='turnstile']",
    "#cf-turnstile",
    ".cf-turnstile",
    "div[id^='cf-turnstile']",
    "div[class*='turnstile']",
    "div[class*='Cloudflare']",
]


class BrowserApplicationEngine:
    """Automates form filling and submission on Greenhouse, Lever, Ashby, and standard ATS pages."""

    def __init__(
        self,
        executable_path: str = "/usr/bin/google-chrome",
        headless: bool = True,
        screenshots_dir: str = "data/screenshots",
        timeout_ms: int = 30000,
        captcha_solver: Optional[CaptchaSolver] = None,
    ):
        self.executable_path = executable_path
        self.headless = headless
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_ms = timeout_ms
        self.captcha_solver = captcha_solver or CaptchaSolver()

    async def _drop_new_tabs(self, page: Page) -> None:
        """Force same-tab navigation across all frames (lessons from career-ops/diagnose.ts).
        
        Strips target=_blank and redirects window.open to in-tab navigation so clicking 'Apply'
        never opens an unfollowed background tab.
        """
        for fr in page.frames:
            try:
                await fr.evaluate("""() => {
                    document.querySelectorAll('a[target="_blank"], a[target="_new"], form[target]').forEach(el => el.removeAttribute('target'));
                    try {
                        window.open = (u) => {
                            if (u) location.href = u;
                            return null;
                        };
                    } catch(e) {}
                }""")
            except Exception:
                pass

    async def _dismiss_cookie_consent(self, page: Page) -> bool:
        """Detect and auto-dismiss cookie/GDPR consent overlays that obscure forms or buttons."""
        for sel in CONSENT_BUTTONS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=1500)
                    logger.info("Dismissed cookie consent banner via selector [%s]", sel)
                    await page.wait_for_timeout(500)
                    return True
            except Exception:
                continue
        return False

    async def _try_apply_trigger(self, page: Page) -> bool:
        """If on a job landing page without immediate form inputs, navigate or click to reveal the form.
        
        Lessons from career-ops:
        1. Look for direct links to known ATS (Ashby, Greenhouse, Lever, etc.)
        2. Look for 'Apply' / 'Apply Now' buttons/links and click them in-tab.
        """
        # 1. Look for <a> linking to known ATS
        try:
            for pattern in ATS_LINK_PATTERNS:
                links = await page.query_selector_all(f"a[href*='{pattern}']")
                for link in links:
                    if not await link.is_visible():
                        continue
                    href = await link.get_attribute("href")
                    if href and href.startswith("http") and "submit" not in href.lower():
                        logger.info("Following direct ATS application link: %s", href)
                        await page.goto(href, wait_until="domcontentloaded", timeout=self.timeout_ms)
                        await page.wait_for_timeout(2000)
                        return True
        except Exception:
            pass

        # 2. Look for Apply CTA buttons or links
        apply_selectors = [
            "button:has-text('Apply for this job')",
            "button:has-text('Apply Now')",
            "a:has-text('Apply for this job')",
            "a:has-text('Apply Now')",
            "button:has-text('Start Application')",
            "button:has-text('Apply')",
            "a[href*='apply' i]:not([href*='mailto'])",
        ]
        for sel in apply_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).lower()
                    if any(w in txt for w in ["submit", "applied", "withdraw"]):
                        continue
                    logger.info("Clicking Apply CTA: [%s]", txt.strip())
                    await self._drop_new_tabs(page)
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(2500)
                    return True
            except Exception:
                continue

        return False

    async def _locate_active_form_context(self, page: Page) -> Union[Page, Frame]:
        """Check top-level page and all child frames for form fields.
        
        Greenhouse and Workable embeds render forms inside an <iframe>.
        Returns the Frame or Page containing the application inputs.
        """
        # First check main page
        inputs = await page.query_selector_all("input[type='text'], input[type='email'], input[name*='name' i]")
        if len(inputs) >= 2:
            return page

        # Search child frames
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_inputs = await frame.query_selector_all("input[type='text'], input[type='email'], input[name*='name' i]")
                if len(frame_inputs) >= 2:
                    logger.info("Found embedded application form inside iframe: %s", frame.url[:80])
                    return frame
            except Exception:
                continue

        return page

    async def _detect_captcha(self, page: Page) -> bool:
        """Check if page currently presents a CAPTCHA or Cloudflare challenge."""
        for selector in CAPTCHA_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _attempt_captcha_resolution(self, page: Page) -> bool:
        """Attempt automated resolution of audio or visual CAPTCHA if presented."""
        audio_buttons = [
            "button#recaptcha-audio-button",
            "button[title*='audio' i]",
            "button[aria-label*='audio' i]",
            "a[href*='sound' i]",
        ]
        for sel in audio_buttons:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    logger.info("Found audio challenge button: %s. Clicking...", sel)
                    await el.click()
                    await page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        img_el = await page.query_selector("img[id*='captcha' i], img[src*='captcha' i]")
        captcha_input = await page.query_selector("input[id*='captcha' i], input[name*='captcha' i]")

        if img_el and captcha_input and await img_el.is_visible():
            try:
                img_bytes = await img_el.screenshot()
                import base64
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                code = await self.captcha_solver.solve_image_challenge(b64)
                if code:
                    await captcha_input.fill(code)
                    logger.info("Filled CAPTCHA input with solved code: %s", code)
                    return True
            except Exception as e:
                logger.warning("Automated CAPTCHA solve error: %s", e)

        return False

    async def _fill_field(self, ctx: Union[Page, Frame], selectors: list[str], value: str) -> bool:
        """Attempt to fill an input field trying multiple CSS/XPath selectors."""
        for sel in selectors:
            try:
                el = await ctx.query_selector(sel)
                if el and await el.is_visible():
                    # Clear first if needed
                    await el.fill(value)
                    return True
            except Exception:
                continue
        return False

    async def _fill_common_fields(self, ctx: Union[Page, Frame], profile: CandidateProfile) -> None:
        """Fill common personal information fields."""
        # First Name
        await self._fill_field(
            ctx,
            ["input[name*='first_name' i]", "input[id*='first_name' i]", "input[aria-label*='first name' i]"],
            profile.first_name,
        )

        # Last Name
        await self._fill_field(
            ctx,
            ["input[name*='last_name' i]", "input[id*='last_name' i]", "input[aria-label*='last name' i]"],
            profile.last_name,
        )

        # Full Name (fallback if no split first/last name)
        await self._fill_field(
            ctx,
            ["input[name*='name' i]:not([name*='first']):not([name*='last'])", "input[id*='name' i]:not([id*='first']):not([id*='last'])"],
            profile.full_name,
        )

        # Email
        await self._fill_field(
            ctx,
            ["input[type='email']", "input[name*='email' i]", "input[id*='email' i]"],
            profile.email,
        )

        # Phone
        await self._fill_field(
            ctx,
            ["input[type='tel']", "input[name*='phone' i]", "input[id*='phone' i]"],
            profile.phone,
        )

        # Location / City
        await self._fill_field(
            ctx,
            ["input[name*='location' i]", "input[id*='location' i]", "input[name*='city' i]"],
            profile.location,
        )

        # LinkedIn
        if profile.linkedin_url:
            await self._fill_field(
                ctx,
                ["input[name*='linkedin' i]", "input[id*='linkedin' i]", "input[aria-label*='linkedin' i]"],
                profile.linkedin_url,
            )

        # GitHub
        if profile.github_url:
            await self._fill_field(
                ctx,
                ["input[name*='github' i]", "input[id*='github' i]", "input[aria-label*='github' i]"],
                profile.github_url,
            )

        # Portfolio / Website
        if profile.portfolio_url:
            await self._fill_field(
                ctx,
                ["input[name*='website' i]", "input[name*='portfolio' i]", "input[id*='website' i]"],
                profile.portfolio_url,
            )

    async def _attach_cv_file(self, ctx: Union[Page, Frame], resume_path: Optional[str]) -> bool:
        """Locate file input and upload candidate CV."""
        if not resume_path or not os.path.exists(resume_path):
            return False

        file_inputs = [
            "input[type='file'][name*='resume' i]",
            "input[type='file'][id*='resume' i]",
            "input[type='file'][aria-label*='resume' i]",
            "input[type='file']",
        ]
        for sel in file_inputs:
            try:
                el = await ctx.query_selector(sel)
                if el:
                    await el.set_input_files(resume_path)
                    logger.info("Attached resume file [%s] via selector [%s]", resume_path, sel)
                    return True
            except Exception as e:
                logger.debug("Failed file input selector %s: %s", sel, e)
                continue
        return False

    async def _get_element_label(self, ctx: Union[Page, Frame], el) -> str:
        """Infer human label or question prompt for an input, textarea, or select."""
        try:
            aria_label = await el.get_attribute("aria-label")
            if aria_label and aria_label.strip():
                return aria_label.strip()

            placeholder = await el.get_attribute("placeholder")
            if placeholder and placeholder.strip() and not any(p in placeholder.lower() for p in ["type", "search", "enter"]):
                return placeholder.strip()

            el_id = await el.get_attribute("id")
            if el_id:
                lbl = await ctx.query_selector(f"label[for='{el_id}']")
                if lbl:
                    txt = await lbl.inner_text()
                    if txt.strip():
                        return txt.strip()

            parent_lbl = await el.evaluate("el => el.closest('label') ? el.closest('label').innerText : ''")
            if parent_lbl and parent_lbl.strip():
                return parent_lbl.strip()

            fg_label = await el.evaluate("""el => {
                const group = el.closest('.form-group, .field, .application-question, div[class*="question"], div[class*="field-entry"], fieldset');
                if (group) {
                    const l = group.querySelector('label, legend, .label, h3, h4, span[class*="label"], [class*="title"]');
                    return l ? l.innerText : '';
                }
                return '';
            }""")
            if fg_label and fg_label.strip():
                return fg_label.strip()

            name_attr = await el.get_attribute("name")
            if name_attr:
                return name_attr.replace("_", " ").title()

        except Exception:
            pass
        return ""

    def _resolve_question_answer(self, label: str, profile: CandidateProfile) -> Optional[str]:
        """Resolve answer to an application question based on profile data and custom answers.
        
        Enhanced with lessons from career-ops/modes/apply.md knockout screening rules.
        """
        if not label:
            return None

        lbl_lower = label.lower()

        # Check explicit custom_answers first
        for k, v in profile.custom_answers.items():
            if k.lower() in lbl_lower or lbl_lower in k.lower():
                return v

        # Work Authorization / Legal Right to Work (Knockout safety)
        if any(w in lbl_lower for w in ["authorized to work", "legally authorized", "right to work", "work permit", "work eligibility", "legal right"]):
            return "Yes"

        # Sponsorship (Knockout safety)
        if any(w in lbl_lower for w in ["sponsorship", "visa sponsorship", "require sponsorship", "require a visa", "future require"]):
            return "No" if not profile.sponsorship_required else "Yes"

        # Remote / Relocation
        if any(w in lbl_lower for w in ["remote", "work remotely", "telecommute"]):
            return "Yes"
        if any(w in lbl_lower for w in ["willing to relocate", "relocation"]):
            return "Open to remote worldwide" if profile.open_to_remote else "No"

        # Years of Experience
        if any(w in lbl_lower for w in ["years of experience", "how many years", "total experience"]):
            return str(profile.years_of_experience)

        # Notice Period / Start Date
        if any(w in lbl_lower for w in ["notice period", "how soon can you start", "available to start", "start date", "earliest start"]):
            return "Immediate / 2 weeks"

        # Salary / Compensation Expectations
        if any(w in lbl_lower for w in ["salary", "compensation", "desired pay", "rate"]):
            return "Competitive / Negotiable"

        # Current Location / Country
        if any(w in lbl_lower for w in ["current location", "where are you based", "city and country", "residence", "country of residence"]):
            return profile.location

        # Age / Over 18
        if any(w in lbl_lower for w in ["18 years", "at least 18", "age of majority"]):
            return "Yes"

        # Background check
        if any(w in lbl_lower for w in ["background check", "drug test"]):
            return "Yes"

        # How did you hear about us
        if any(w in lbl_lower for w in ["how did you hear", "source", "referral"]):
            return "LinkedIn"

        # Voluntary Self-Identification / Demographics (EEO)
        if any(w in lbl_lower for w in ["gender", "race", "ethnicity", "veteran", "disability", "sexual orientation"]):
            return "I choose not to disclose"

        return None

    async def _fill_questionnaire(self, ctx: Union[Page, Frame], profile: CandidateProfile) -> Dict[str, str]:
        """Dynamically detect and answer custom questions, dropdowns, comboboxes, and radios."""
        answers_captured: Dict[str, str] = {}

        # 1. Custom text inputs and textareas
        custom_inputs = await ctx.query_selector_all("input[type='text'], textarea")
        for inp in custom_inputs:
            try:
                val = await inp.input_value()
                if val:
                    continue

                label_text = await self._get_element_label(ctx, inp)
                if not label_text:
                    continue

                ans = self._resolve_question_answer(label_text, profile)
                if ans:
                    await inp.fill(ans)
                    answers_captured[label_text] = ans
            except Exception:
                continue

        # 2. Dropdowns (<select>)
        selects = await ctx.query_selector_all("select")
        for sel in selects:
            try:
                label_text = await self._get_element_label(ctx, sel)
                ans = self._resolve_question_answer(label_text, profile)

                options = await sel.query_selector_all("option")
                opt_texts = [await o.inner_text() for o in options]

                target_option = None
                if ans:
                    ans_lower = ans.lower()
                    for t in opt_texts:
                        if t.strip().lower() == ans_lower or ans_lower in t.strip().lower():
                            target_option = t.strip()
                            break

                if not target_option and any(w in label_text.lower() for w in ["gender", "race", "veteran", "disability"]):
                    for t in opt_texts:
                        if any(d in t.lower() for d in ["decline", "prefer not", "choose not", "don't wish"]):
                            target_option = t.strip()
                            break

                if target_option:
                    await sel.select_option(label=target_option)
                    answers_captured[label_text or "select"] = target_option
            except Exception:
                continue

        # 2b. Custom comboboxes / React-Select (Greenhouse & Ashby pattern)
        comboboxes = await ctx.query_selector_all("[role='combobox'], div[class*='select__control'], div[class*='react-select']")
        for cb in comboboxes:
            try:
                label_text = await self._get_element_label(ctx, cb)
                ans = self._resolve_question_answer(label_text, profile)
                if ans and await cb.is_visible():
                    await cb.click(timeout=1500)
                    await asyncio.sleep(0.3)
                    # Type answer and press enter
                    await ctx.page.keyboard.type(ans)
                    await asyncio.sleep(0.3)
                    await ctx.page.keyboard.press("Enter")
                    answers_captured[label_text or "combobox"] = ans
            except Exception:
                continue

        # 3. Radio button groups
        radios = await ctx.query_selector_all("input[type='radio']")
        handled_groups = set()
        for radio in radios:
            try:
                group_name = await radio.get_attribute("name")
                if not group_name or group_name in handled_groups:
                    continue
                handled_groups.add(group_name)

                group_label = await self._get_element_label(ctx, radio)
                ans = self._resolve_question_answer(group_label, profile)
                if not ans:
                    continue

                group_radios = await ctx.query_selector_all(f"input[type='radio'][name='{group_name}']")
                for r in group_radios:
                    r_lbl = await self._get_element_label(ctx, r)
                    r_val = (await r.get_attribute("value") or "").lower()
                    if ans.lower() in r_lbl.lower() or ans.lower() == r_val:
                        await r.check()
                        answers_captured[group_label or group_name] = ans
                        break
            except Exception:
                continue

        return answers_captured

    async def _detect_cloudflare_turnstile(self, page: Page) -> bool:
        """Check if page presents a Cloudflare Turnstile challenge."""
        for selector in CLOUDFLARE_TURNSTILE_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _handle_cloudflare_turnstile(self, page: Page) -> bool:
        """Attempt to resolve Cloudflare Turnstile challenge."""
        try:
            # Try clicking the Turnstile iframe
            turnstile_frame = await page.query_selector("iframe[src*='turnstile']")
            if turnstile_frame:
                await turnstile_frame.click(timeout=3000)
                await page.wait_for_timeout(2000)
                return True
            # Try the challenge container
            for sel in CLOUDFLARE_TURNSTILE_SELECTORS[1:]:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(2000)
                    return True
        except Exception:
            pass
        return False

    async def _try_linkedin_easy_apply(self, page: Page) -> bool:
        """Handle LinkedIn's 'Easy Apply' flow which uses a side panel React modal."""
        for sel in LINKEDIN_EASY_APPLY_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    # Wait for any modal animation
                    await page.wait_for_timeout(1000)
                    await el.click(timeout=5000)
                    await page.wait_for_timeout(3000)
                    # Check if side panel opened
                    side_panel = await page.query_selector('[data-control-name="jobapply-details"]')
                    if not side_panel:
                        side_panel = await page.query_selector('.jobs-easy-apply-container, [class*="apply-container"]')
                    if side_panel:
                        logger.info("LinkedIn Easy Apply panel opened successfully")
                        await page.wait_for_timeout(2000)
                        return True
                    # Fallback: check for iframe overlay
                    iframe = await page.query_selector('iframe[src*="linkedin"]')
                    if iframe:
                        logger.info("LinkedIn application iframe detected")
                        await page.wait_for_timeout(2000)
                        return True
                    return True
            except Exception:
                continue
        return False

    async def _fill_linkedin_skills(self, page: Page, profile: CandidateProfile) -> bool:
        """Add skills tags on LinkedIn's application form if present."""
        skills_added = 0
        for skill in profile.verified_skills[:5]:
            try:
                # Look for skill input/combobox
                skill_input = await page.query_selector(
                    'input[placeholder*="skill" i], input[placeholder*="search" i], [role="combobox"]'
                )
                if skill_input and await skill_input.is_visible():
                    await skill_input.fill(skill)
                    await page.wait_for_timeout(500)
                    # Press Enter to add the tag
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(500)
                    skills_added += 1
                    logger.info("Added LinkedIn skill tag: %s", skill)
            except Exception:
                continue
        return skills_added > 0

    async def _handle_steppers_and_submit(
        self,
        ctx: Union[Page, Frame],
        page: Page,
        profile: CandidateProfile,
        max_steps: int = 5,
    ) -> Tuple[Optional[Any], bool]:
        """Handle multi-page/stepper applications by clicking 'Next' until 'Submit' is reached."""
        for step in range(max_steps):
            # Check for final submit button
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "input#nextButton",
                "button:has-text('Submit Application')",
                "button:has-text('Submit application')",
                "button:has-text('Submit')",
                "button:has-text('Apply')",
                "button:has-text('Send Application')",
                "button:has-text('Complete Application')",
                "button:has-text('Continue application')",
                "#submit_app",
                "[data-qa='btn-submit']",
                "button:has-text('Save and Continue')",
            ]
            for sel in submit_selectors:
                try:
                    el = await ctx.query_selector(sel)
                    if el and await el.is_visible():
                        txt = (await el.inner_text() or await el.get_attribute("value") or "").lower()
                        if any(w in txt for w in ["submit", "apply", "send", "finish", "continue"]):
                            return el, True
                except Exception:
                    continue

            # If no submit button, check for Next / Continue / Step button
            next_selectors = [
                "button:has-text('Next')",
                "button:has-text('Continue')",
                "button:has-text('Save & Continue')",
                "button:has-text('Review')",
                "button:has-text('Next Step')",
                "input[value*='Next' i]",
                "input[value*='Continue' i]",
                "button:has-text('Save and continue')",
            ]
            next_btn = None
            for sel in next_selectors:
                try:
                    el = await ctx.query_selector(sel)
                    if el and await el.is_visible():
                        next_btn = el
                        break
                except Exception:
                    continue

            if next_btn:
                logger.info("Stepper progress: clicking Next / Continue on step %d", step + 1)
                await next_btn.click()
                await page.wait_for_timeout(2000)
                await self._fill_common_fields(ctx, profile)
                await self._fill_questionnaire(ctx, profile)
            else:
                break

        return None, False

    async def fill_and_submit(
        self,
        job: JobPosting,
        profile: CandidateProfile,
        resume_file_path: Optional[str] = None,
        dry_run: bool = False,
    ) -> ApplicationRecord:
        """Navigate to application page, fill form, and autonomously submit or dry-run."""
        record = ApplicationRecord(
            job_id=job.id or 0,
            state=JobState.APPLICATION_STARTED,
            submission_payload={"url": job.raw_url, "dry_run": dry_run},
        )

        async with async_playwright() as p:
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
            exec_path = self.executable_path if os.path.exists(self.executable_path) else None
            browser = await p.chromium.launch(
                executable_path=exec_path,
                headless=self.headless,
                args=launch_args,
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()

            # Inject stealth evasions to mask Playwright/Selenium traces
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """)

            try:
                logger.info("Navigating to application URL: %s", job.raw_url)
                await page.goto(job.raw_url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                # Strip target=_blank across all frames so navigation stays in-tab
                await self._drop_new_tabs(page)

                # Auto-dismiss cookie/GDPR consent banner
                await self._dismiss_cookie_consent(page)

                # Detect if this is a LinkedIn job page
                is_linkedin = "linkedin.com" in job.raw_url.lower()

                if is_linkedin:
                    logger.info("LinkedIn job detected - using LinkedIn-specific application flow")

                # 1. Check for CAPTCHA (including Cloudflare Turnstile) and attempt resolution
                captcha_detected = await self._detect_captcha(page) or (is_linkedin and await self._detect_cloudflare_turnstile(page))
                if captcha_detected:
                    logger.info("CAPTCHA detected on job %s. Attempting automated resolution...", job.id)
                    if is_linkedin:
                        turnstile_solved = await self._handle_cloudflare_turnstile(page)
                        if turnstile_solved:
                            await page.wait_for_timeout(2000)
                            captcha_detected = await self._detect_captcha(page) or await self._detect_cloudflare_turnstile(page)
                    solved = await self._attempt_captcha_resolution(page) if captcha_detected else False
                    if not solved:
                        screenshot_file = str(self.screenshots_dir / f"captcha_job_{job.id}.png")
                        await page.screenshot(path=screenshot_file, full_page=True)
                        record.state = JobState.BLOCKED_CAPTCHA
                        record.screenshot_path = screenshot_file
                        record.error_message = "CAPTCHA or Cloudflare challenge detected and unresolvable"
                        logger.warning("Job %s blocked by CAPTCHA: %s", job.id, screenshot_file)
                        return record

                # 2. Handle LinkedIn Easy Apply flow
                if is_linkedin:
                    easy_apply_opened = await self._try_linkedin_easy_apply(page)
                    if easy_apply_opened:
                        logger.info("LinkedIn Easy Apply opened - filling skills and form")
                        await self._fill_common_fields(page, profile)
                        await self._fill_linkedin_skills(page, profile)
                        await page.wait_for_timeout(2000)

                # 3. Check if form inputs exist; if not, trigger 'Apply' CTA or follow direct ATS link
                if not is_linkedin:
                    initial_inputs = await page.query_selector_all("input[type='text'], input[type='email']")
                    if len(initial_inputs) < 2:
                        logger.info("No direct form inputs on landing page for Job %s. Triggering Apply CTA...", job.id)
                        triggered = await self._try_apply_trigger(page)
                        if triggered:
                            await self._drop_new_tabs(page)
                            await self._dismiss_cookie_consent(page)

                # 4. Locate active form context (top page or embedded iframe)
                if not is_linkedin:
                    ctx = await self._locate_active_form_context(page)

                # 4. Fill form fields
                await self._fill_common_fields(ctx, profile)

                # 5. Fill dynamic questionnaire (questions, radios, dropdowns, comboboxes)
                answers = await self._fill_questionnaire(ctx, profile)
                if answers:
                    record.submission_payload["answers"] = answers
                    logger.info("Answered %d dynamic form questions on job %s", len(answers), job.id)

                # 6. Attach CV file
                if resume_file_path:
                    await self._attach_cv_file(ctx, resume_file_path)

                # If dry-run mode, capture screenshot and return success without clicking submit
                if dry_run:
                    screenshot_file = str(self.screenshots_dir / f"dry_run_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file, full_page=True)
                    record.state = JobState.SUBMITTED
                    record.screenshot_path = screenshot_file
                    record.confirmation_text = "DRY RUN: Form filled and verified successfully without submit."
                    return record

                # 7. Handle multi-step forms and locate final submit button
                submit_btn, found = await self._handle_steppers_and_submit(ctx, page, profile)

                if not submit_btn:
                    screenshot_file = str(self.screenshots_dir / f"no_submit_btn_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file)
                    record.state = JobState.FAILED
                    record.screenshot_path = screenshot_file
                    record.error_message = "Could not locate a visible Submit button"
                    return record

                # 8. Click submit with scroll-into-view and fallback
                try:
                    await submit_btn.scroll_into_view_if_needed()
                    await submit_btn.click(timeout=5000)
                except Exception:
                    # Fallback to evaluating click in element's context
                    await submit_btn.evaluate("el => el.click()")

                await page.wait_for_timeout(5000)

                # 9. Check for submission errors vs confirmation
                content = await page.content()
                if is_captcha_error(content):
                    screenshot_file = str(self.screenshots_dir / f"captcha_reject_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file)
                    record.state = JobState.BLOCKED_CAPTCHA
                    record.screenshot_path = screenshot_file
                    record.error_message = "Submission rejected: Invalid CAPTCHA"
                    return record

                # Check for field validation errors
                field_errors = await page.query_selector_all(".error, [aria-invalid='true'], .field-error, .application-error")
                visible_errors = []
                for fe in field_errors:
                    if await fe.is_visible():
                        visible_errors.append((await fe.inner_text()).strip())

                page_text = content.lower()
                current_url = page.url.lower()

                has_success = (
                    any(ind in page_text for ind in SUCCESS_INDICATORS)
                    or "thank" in current_url
                    or "confirm" in current_url
                    or "success" in current_url
                    or "applied" in current_url
                )

                screenshot_file = str(self.screenshots_dir / f"submission_result_job_{job.id}.png")
                await page.screenshot(path=screenshot_file)
                record.screenshot_path = screenshot_file

                if has_success:
                    record.state = JobState.SUBMITTED
                    record.confirmation_text = "Application submitted and confirmed"
                elif visible_errors:
                    record.state = JobState.FAILED
                    record.error_message = f"Field validation errors: {'; '.join(visible_errors[:3])}"
                else:
                    record.state = JobState.FAILED
                    record.error_message = "Submitted, but could not detect confirmation text"

                return record

            except Exception as e:
                logger.error("Browser automation error for job %s: %s", job.id, e)
                screenshot_file = str(self.screenshots_dir / f"error_job_{job.id}.png")
                try:
                    await page.screenshot(path=screenshot_file)
                    record.screenshot_path = screenshot_file
                except Exception:
                    pass
                record.state = JobState.FAILED
                record.error_message = str(e)
                return record

            finally:
                await context.close()
                await browser.close()
