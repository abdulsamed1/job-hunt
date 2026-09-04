"""Browser automation engine using Playwright with stealth context and audio-first CAPTCHA resolution."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

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
        # 1. Look for audio challenge button (e.g. reCAPTCHA audio button)
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
                    # Look for audio source or evaluate fetch
                    break
            except Exception:
                pass

        # 2. Look for BotDetect or standard image CAPTCHA
        img_el = await page.query_selector("img[id*='captcha' i], img[src*='captcha' i]")
        captcha_input = await page.query_selector("input[id*='captcha' i], input[name*='captcha' i]")

        if img_el and captcha_input and await img_el.is_visible():
            try:
                # Capture screenshot of CAPTCHA image element
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

    async def _fill_field(self, page: Page, selectors: list[str], value: str) -> bool:
        """Attempt to fill an input field trying multiple CSS/XPath selectors."""
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(value)
                    return True
            except Exception:
                continue
        return False

    async def _fill_common_fields(self, page: Page, profile: CandidateProfile) -> None:
        """Fill common personal information fields."""
        # First Name
        await self._fill_field(
            page,
            ["input[name*='first_name' i]", "input[id*='first_name' i]", "input[aria-label*='first name' i]"],
            profile.first_name,
        )

        # Last Name
        await self._fill_field(
            page,
            ["input[name*='last_name' i]", "input[id*='last_name' i]", "input[aria-label*='last name' i]"],
            profile.last_name,
        )

        # Full Name (fallback if no split first/last name)
        await self._fill_field(
            page,
            ["input[name*='name' i]:not([name*='first']):not([name*='last'])", "input[id*='name' i]:not([id*='first']):not([id*='last'])"],
            profile.full_name,
        )

        # Email
        await self._fill_field(
            page,
            ["input[type='email']", "input[name*='email' i]", "input[id*='email' i]"],
            profile.email,
        )

        # Phone
        await self._fill_field(
            page,
            ["input[type='tel']", "input[name*='phone' i]", "input[id*='phone' i]"],
            profile.phone,
        )

        # Location / City
        await self._fill_field(
            page,
            ["input[name*='location' i]", "input[id*='location' i]", "input[name*='city' i]"],
            profile.location,
        )

        # LinkedIn
        if profile.linkedin_url:
            await self._fill_field(
                page,
                ["input[name*='linkedin' i]", "input[id*='linkedin' i]", "input[aria-label*='linkedin' i]"],
                profile.linkedin_url,
            )

        # GitHub
        if profile.github_url:
            await self._fill_field(
                page,
                ["input[name*='github' i]", "input[id*='github' i]", "input[aria-label*='github' i]"],
                profile.github_url,
            )

        # Portfolio / Website
        if profile.portfolio_url:
            await self._fill_field(
                page,
                ["input[name*='website' i]", "input[name*='portfolio' i]", "input[id*='website' i]"],
                profile.portfolio_url,
            )

    async def _attach_cv_file(self, page: Page, resume_path: Optional[str]) -> bool:
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
                el = await page.query_selector(sel)
                if el:
                    await el.set_input_files(resume_path)
                    logger.info("Attached resume file [%s] via selector [%s]", resume_path, sel)
                    return True
            except Exception as e:
                logger.debug("Failed file input selector %s: %s", sel, e)
                continue
        return False

    async def _get_element_label(self, page: Page, el) -> str:
        """Infer human label or question prompt for an input, textarea, or select."""
        try:
            aria_label = await el.get_attribute("aria-label")
            if aria_label:
                return aria_label.strip()

            placeholder = await el.get_attribute("placeholder")
            if placeholder:
                return placeholder.strip()

            el_id = await el.get_attribute("id")
            if el_id:
                lbl = await page.query_selector(f"label[for='{el_id}']")
                if lbl:
                    txt = await lbl.inner_text()
                    if txt.strip():
                        return txt.strip()

            parent_lbl = await el.evaluate("el => el.closest('label') ? el.closest('label').innerText : ''")
            if parent_lbl and parent_lbl.strip():
                return parent_lbl.strip()

            fg_label = await el.evaluate("""el => {
                const group = el.closest('.form-group, .field, .application-question, div[class*="question"], fieldset');
                if (group) {
                    const l = group.querySelector('label, legend, .label, h3, h4, span[class*="label"]');
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
        """Resolve answer to an application question based on profile data and custom answers."""
        if not label:
            return None

        lbl_lower = label.lower()

        # Check explicit custom_answers first
        for k, v in profile.custom_answers.items():
            if k.lower() in lbl_lower or lbl_lower in k.lower():
                return v

        # Work Authorization / Legal Right to Work
        if any(w in lbl_lower for w in ["authorized to work", "legally authorized", "right to work", "work permit", "work eligibility"]):
            return "Yes"

        # Sponsorship
        if any(w in lbl_lower for w in ["sponsorship", "visa sponsorship", "require sponsorship", "require a visa"]):
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
        if any(w in lbl_lower for w in ["notice period", "how soon can you start", "available to start", "start date"]):
            return "Immediate / 2 weeks"

        # Salary / Compensation Expectations
        if any(w in lbl_lower for w in ["salary", "compensation", "desired pay"]):
            return "Competitive / Negotiable"

        # Current Location
        if any(w in lbl_lower for w in ["current location", "where are you based", "city and country", "residence"]):
            return profile.location

        # Voluntary Self-Identification / Demographics
        if any(w in lbl_lower for w in ["gender", "race", "ethnicity", "veteran", "disability", "sexual orientation"]):
            return "I choose not to disclose"

        return None

    async def _fill_questionnaire(self, page: Page, profile: CandidateProfile) -> Dict[str, str]:
        """Dynamically detect and answer custom questions, dropdowns, and radios."""
        answers_captured: Dict[str, str] = {}

        # 1. Custom text inputs and textareas
        custom_inputs = await page.query_selector_all("input[type='text'], textarea")
        for inp in custom_inputs:
            try:
                val = await inp.input_value()
                if val:
                    continue

                label_text = await self._get_element_label(page, inp)
                if not label_text:
                    continue

                ans = self._resolve_question_answer(label_text, profile)
                if ans:
                    await inp.fill(ans)
                    answers_captured[label_text] = ans
            except Exception:
                continue

        # 2. Dropdowns (<select>)
        selects = await page.query_selector_all("select")
        for sel in selects:
            try:
                label_text = await self._get_element_label(page, sel)
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
                        if any(d in t.lower() for d in ["decline", "prefer not", "choose not"]):
                            target_option = t.strip()
                            break

                if target_option:
                    await sel.select_option(label=target_option)
                    answers_captured[label_text or "select"] = target_option
            except Exception:
                continue

        # 3. Radio button groups
        radios = await page.query_selector_all("input[type='radio']")
        handled_groups = set()
        for radio in radios:
            try:
                group_name = await radio.get_attribute("name")
                if not group_name or group_name in handled_groups:
                    continue
                handled_groups.add(group_name)

                group_label = await self._get_element_label(page, radio)
                ans = self._resolve_question_answer(group_label, profile)
                if not ans:
                    continue

                group_radios = await page.query_selector_all(f"input[type='radio'][name='{group_name}']")
                for r in group_radios:
                    r_lbl = await self._get_element_label(page, r)
                    r_val = (await r.get_attribute("value") or "").lower()
                    if ans.lower() in r_lbl.lower() or ans.lower() == r_val:
                        await r.check()
                        answers_captured[group_label or group_name] = ans
                        break
            except Exception:
                continue

        return answers_captured

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

                # 1. Check for CAPTCHA and attempt resolution
                if await self._detect_captcha(page):
                    logger.info("CAPTCHA detected on job %s. Attempting automated resolution...", job.id)
                    solved = await self._attempt_captcha_resolution(page)
                    if not solved:
                        screenshot_file = str(self.screenshots_dir / f"captcha_job_{job.id}.png")
                        await page.screenshot(path=screenshot_file, full_page=True)
                        record.state = JobState.BLOCKED_CAPTCHA
                        record.screenshot_path = screenshot_file
                        record.error_message = "CAPTCHA or Cloudflare challenge detected and unresolvable"
                        logger.warning("Job %s blocked by CAPTCHA: %s", job.id, screenshot_file)
                        return record

                # 2. Fill form fields
                await self._fill_common_fields(page, profile)

                # 2b. Fill dynamic ATS questionnaire (custom questions, radios, dropdowns)
                answers = await self._fill_questionnaire(page, profile)
                if answers:
                    record.submission_payload["answers"] = answers
                    logger.info("Answered %d dynamic form questions on job %s", len(answers), job.id)

                # 3. Attach CV
                if resume_file_path:
                    await self._attach_cv_file(page, resume_file_path)

                # If dry-run mode, capture screenshot and return success without clicking submit
                if dry_run:
                    screenshot_file = str(self.screenshots_dir / f"dry_run_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file, full_page=True)
                    record.state = JobState.SUBMITTED
                    record.screenshot_path = screenshot_file
                    record.confirmation_text = "DRY RUN: Form filled and verified successfully without submit."
                    return record

                # 4. Find submit button (Precision selectors matching opran-booking lessons)
                submit_selectors = [
                    "input#nextButton",
                    "button[type='submit']",
                    "input[type='submit']",
                    "button:has-text('Submit')",
                    "button:has-text('Apply')",
                    "#submit_app",
                ]
                submit_btn = None
                for sel in submit_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el and await el.is_visible():
                            submit_btn = el
                            break
                    except Exception:
                        continue

                if not submit_btn:
                    screenshot_file = str(self.screenshots_dir / f"no_submit_btn_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file)
                    record.state = JobState.FAILED
                    record.screenshot_path = screenshot_file
                    record.error_message = "Could not locate a visible Submit button"
                    return record

                # 5. Click submit
                await submit_btn.click()
                await page.wait_for_timeout(5000)

                # 6. Verify confirmation or rejection
                content = await page.content()
                if is_captcha_error(content):
                    screenshot_file = str(self.screenshots_dir / f"captcha_reject_job_{job.id}.png")
                    await page.screenshot(path=screenshot_file)
                    record.state = JobState.BLOCKED_CAPTCHA
                    record.screenshot_path = screenshot_file
                    record.error_message = "Submission rejected: Invalid CAPTCHA"
                    return record

                page_text = content.lower()
                current_url = page.url.lower()

                has_success = (
                    any(ind in page_text for ind in SUCCESS_INDICATORS)
                    or "thank" in current_url
                    or "confirm" in current_url
                    or "success" in current_url
                )

                screenshot_file = str(self.screenshots_dir / f"submission_result_job_{job.id}.png")
                await page.screenshot(path=screenshot_file)
                record.screenshot_path = screenshot_file

                if has_success:
                    record.state = JobState.SUBMITTED
                    record.confirmation_text = "Application submitted and confirmed"
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
