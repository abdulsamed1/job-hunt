#!/usr/bin/env python3
"""Interactive LinkedIn login — run ONCE on a machine with a screen (your laptop).

Opens a real browser window on linkedin.com/login. YOU log in yourself, directly
on LinkedIn's site — this script never sees your password. When LinkedIn lands
on your feed, the script saves the login session to data/linkedin_state.json
(owner-only permissions) and exits. The pipeline reuses it headlessly.

Your LinkedIn password is never typed into, shown to, or stored by this script.
Session file is gitignored (data/) — never commit or share it.

Usage:
    python scripts/linkedin_login.py [--state-file data/linkedin_state.json]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def login(state_file: str, timeout_sec: int = 300) -> int:
    from playwright.async_api import async_playwright

    print("Opening LinkedIn login in a real browser window…")
    print("Log in yourself on linkedin.com. Waiting up to 5 minutes.")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        # Wait until the session cookie appears (login completed by the human)
        li_at = None
        for _ in range(timeout_sec):
            cookies = await context.cookies()
            li_at = next((c for c in cookies if c.get("name") == "li_at"), None)
            if li_at:
                break
            await asyncio.sleep(1)

        if not li_at:
            print("ERROR: timed out waiting for login. No session saved.")
            await browser.close()
            return 1

        target = Path(state_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(target))
        os.chmod(str(target), 0o600)
        await browser.close()

    print(f"Saved LinkedIn session to {target} (mode 0600).")
    print("The pipeline will now reuse this login headlessly.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", default="data/linkedin_state.json")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    return asyncio.run(login(args.state_file, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
