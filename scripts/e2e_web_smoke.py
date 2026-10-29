"""End-to-end smoke test: real API + exported Expo web bundle + headless Chromium.

Usage (from repo root, after `npx expo export --platform web` in apps/mobile):
    python scripts/e2e_web_smoke.py --dist apps/mobile/dist [--screenshots out/]

Flow: onboarding -> register -> verify (code read from the dev API log) -> home
dashboard (DEMO badge visible) -> each tab -> settings -> logout.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):  # type: ignore[no-untyped-def]
        path = Path(self.translate_path(self.path))
        if not path.exists():
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, *args):  # type: ignore[no-untyped-def]
        pass


def wait_for(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(url, timeout=1).status == 200:  # noqa: S310
                return
        except Exception:  # noqa: S112
            time.sleep(0.3)
    raise RuntimeError(f"Timed out waiting for {url}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True)
    ap.add_argument("--screenshots")
    ap.add_argument("--web-port", type=int, default=8081)
    args = ap.parse_args()
    web_url = f"http://localhost:{args.web_port}"
    shots = Path(args.screenshots) if args.screenshots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp())
    log_path = tmp / "api.log"
    env = {**os.environ, "ENVIRONMENT": "development",
           "DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}", "OTP_DELIVERY": "console",
           "CORS_ORIGINS": web_url}
    backend = ROOT / "backend"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env,
                   check=True, capture_output=True)
    log = open(log_path, "w")  # noqa: SIM115
    api = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"],
        cwd=backend, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    handler = functools.partial(SpaHandler, directory=str(Path(args.dist).resolve()))
    web = http.server.ThreadingHTTPServer(("127.0.0.1", args.web_port), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    email = f"e2e{int(time.time())}@example.com"
    try:
        wait_for(f"{API}/api/v1/health/live")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(web_url)

            page.get_by_role("button", name="Skip").click()
            page.get_by_test_id("welcome-register").click()
            page.get_by_test_id("register-name").fill("Asha Trader")
            page.get_by_test_id("register-identifier").fill(email)
            page.get_by_test_id("register-password").fill("Str0ngPassw0rd!")
            page.get_by_role("switch", name="I understand and accept these risks").click()
            if shots:
                page.screenshot(path=str(shots / "01-register.png"), full_page=True)
            page.get_by_test_id("register-submit").click()

            expect(page.get_by_text("Verify your account")).to_be_visible()
            code = None
            for _ in range(30):
                m = re.search(rf"{re.escape(email)} via EMAIL \(VERIFY_ACCOUNT\): (\d{{6}})",
                              log_path.read_text())
                if m:
                    code = m.group(1)
                    break
                time.sleep(0.2)
            assert code, "verification code not found in dev API log"
            page.get_by_test_id("verify-code").fill(code)
            page.get_by_test_id("verify-submit").click()

            expect(page.get_by_text("Hi, Asha")).to_be_visible(timeout=10_000)
            expect(page.get_by_test_id("badge-demo").first).to_be_visible(timeout=10_000)
            expect(page.get_by_test_id("demo-disclaimer")).to_be_visible()
            expect(page.get_by_test_id("trading-mode-banner")).to_contain_text("LIVE LOCKED")
            if shots:
                page.screenshot(path=str(shots / "02-home.png"), full_page=True)

            for tab, heading in [("Markets", "Markets"), ("AI Lab", "AI Lab"),
                                 ("Bots", "Bots"), ("Portfolio", "Portfolio")]:
                page.get_by_role("tab", name=tab).click()
                expect(page.get_by_role("heading", name=heading)).to_be_visible()
            if shots:
                page.screenshot(path=str(shots / "03-portfolio.png"), full_page=True)

            page.get_by_role("tab", name="Home").click()
            page.get_by_role("button", name="Profile and settings").click()
            expect(page.get_by_text("App preferences")).to_be_visible(timeout=10_000)
            if shots:
                page.screenshot(path=str(shots / "04-settings.png"), full_page=True)
            page.get_by_test_id("logout").click()
            page.get_by_role("alert").get_by_role("button", name="Log out").click()
            expect(page.get_by_test_id("welcome-login")).to_be_visible(timeout=10_000)
            browser.close()
        print("E2E web smoke test passed")
        return 0
    finally:
        api.terminate()
        web.shutdown()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
