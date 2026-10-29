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
    ap.add_argument("--live-fake", action="store_true",
                    help="Run the API in INDSTOCKS mode against the in-memory INDstocks fake")
    args = ap.parse_args()
    web_url = f"http://localhost:{args.web_port}"
    shots = Path(args.screenshots) if args.screenshots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    email = f"e2e{int(time.time())}@example.com"
    tmp = Path(tempfile.mkdtemp())
    log_path = tmp / "api.log"
    env = {**os.environ, "ENVIRONMENT": "development",
           "DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}", "OTP_DELIVERY": "console",
           "CORS_ORIGINS": web_url, "BOT_WORKER_EMBEDDED": "true",
           "BOT_WORKER_INTERVAL_SECONDS": "2"}
    if args.live_fake:
        env |= {"MARKET_DATA_MODE": "INDSTOCKS", "INDSTOCKS_DATA_USER_EMAILS": email}
    backend = ROOT / "backend"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env,
                   check=True, capture_output=True)
    log = open(log_path, "w")  # noqa: SIM115
    cmd = ([sys.executable, str(ROOT / "scripts" / "fake_live_api.py")] if args.live_fake
           else [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"])
    api = subprocess.Popen(  # noqa: S603
        cmd, cwd=backend, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    handler = functools.partial(SpaHandler, directory=str(Path(args.dist).resolve()))
    web = http.server.ThreadingHTTPServer(("127.0.0.1", args.web_port), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()

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
            badge = "badge-live" if args.live_fake else "badge-demo"
            expect(page.get_by_test_id(badge).first).to_be_visible(timeout=10_000)
            if not args.live_fake:
                expect(page.get_by_test_id("demo-disclaimer")).to_be_visible()
            expect(page.get_by_test_id("trading-mode-banner")).to_contain_text("LIVE LOCKED")
            if shots:
                page.screenshot(path=str(shots / "02-home.png"), full_page=True)

            def shot(name: str) -> None:
                if shots:
                    page.screenshot(path=str(shots / f"{name}.png"), full_page=True)

            # Option chain from the Home quick action.
            page.get_by_test_id("qa-options").click()
            expect(page.get_by_test_id("option-chain-table")).to_be_visible(timeout=10_000)
            if args.live_fake:
                expect(page.get_by_text("Lot size: 75")).to_be_visible()
            else:
                expect(page.get_by_test_id("chain-metadata-note")).to_contain_text("Lot size")
            shot("03-option-chain")
            page.go_back()

            # Markets -> chart.
            page.get_by_role("tab", name="Markets").click()
            expect(page.get_by_role("heading", name="Markets")).to_be_visible()
            shot("04-markets")
            page.get_by_test_id("quote-NIFTY").click()
            expect(page.get_by_test_id("candle-chart")).to_be_visible(timeout=10_000)
            expect(page.get_by_test_id("toggle-smc")).to_be_visible()
            if args.live_fake:
                expect(page.get_by_test_id("chart-proxy-note")).to_contain_text("futures")
            shot("05-chart")
            page.go_back()

            # AI analysis abstains.
            page.get_by_role("tab", name="AI Lab").click()
            expect(page.get_by_test_id("analysis-decision")).to_contain_text("NO TRADE",
                                                                              timeout=10_000)
            expect(page.get_by_test_id("regime-card")).to_be_visible()
            expect(page.get_by_test_id("probability-card")).to_contain_text("NOT AVAILABLE")
            expect(page.get_by_test_id("mtf-strip")).to_be_visible(timeout=10_000)
            shot("06-ai-analysis")
            page.get_by_test_id("strategies-card").get_by_role("button").first.click()
            expect(page.get_by_test_id("strategies-card")).to_contain_text("Momentum")
            page.get_by_role("button", name="AI Model Lab").click()
            expect(page.get_by_text("No models trained yet")).to_be_visible(timeout=10_000)
            shot("06b-model-lab")
            page.go_back()

            # AI Auto Create -> saved draft -> validation blocks activation.
            page.get_by_role("tab", name="Bots").click()
            page.get_by_test_id("create-bot").click()
            page.get_by_test_id("new-ai").click()
            page.get_by_role("button", name="Next").click()
            page.get_by_test_id("ai-generate").click()
            expect(page.get_by_text("Why this configuration")).to_be_visible(timeout=10_000)
            shot("07-ai-create-review")
            page.get_by_test_id("ai-save").click()
            expect(page.get_by_test_id("validation-report")).to_contain_text(
                "READY (SIGNAL-ONLY)", timeout=10_000)
            expect(page.get_by_test_id("validation-report")).to_contain_text("ORDERS BLOCKED")
            page.get_by_test_id("bot-start").click()
            page.get_by_role("alert").get_by_role("button", name="Start").click()
            expect(page.get_by_test_id("bot-status")).to_contain_text("ACTIVE", timeout=10_000)
            # The embedded worker evaluates the bot within a few seconds.
            expect(page.get_by_test_id("bot-health")).to_contain_text("HEALTHY", timeout=30_000)
            shot("08-bot-running")
            page.get_by_test_id("bot-stop").click()
            page.get_by_role("alert").get_by_role("button", name="Stop").click()
            expect(page.get_by_test_id("bot-status")).to_contain_text("STOPPED", timeout=10_000)
            expect(page.get_by_role("alert")).to_have_count(0, timeout=5_000)
            page.get_by_role("tab", name="Logs").click()
            expect(page.get_by_test_id("bot-feed")).to_contain_text("Stopped by user")
            shot("08b-bot-logs")
            page.go_back()
            page.go_back()
            expect(page.get_by_role("heading", name="My Bots")).to_be_visible()
            page.get_by_role("tab", name=re.compile("Stopped")).click()
            expect(page.get_by_role("button", name=re.compile(r"^NIFTY .* (CE|PE), STOPPED"))).to_be_visible(timeout=10_000)
            shot("09-my-bots")

            # Portfolio -> Risk Center -> kill switch.
            page.get_by_role("tab", name="Portfolio").click()
            expect(page.get_by_test_id("portfolio-summary")).to_be_visible(timeout=10_000)
            shot("10-portfolio")
            page.get_by_role("button", name="Risk Center").click()
            page.get_by_test_id("kill-switch").click()
            page.get_by_role("alert").get_by_role("button", name="Engage").click()
            expect(page.get_by_role("button", name="Release kill switch")).to_be_visible(
                timeout=10_000)
            expect(page.get_by_role("alert")).to_have_count(0, timeout=5_000)
            shot("11-risk-center")
            page.go_back()

            page.get_by_role("tab", name="Home").click()
            page.get_by_role("button", name="Profile and settings").click()
            expect(page.get_by_text("App preferences")).to_be_visible(timeout=10_000)
            shot("12-settings")
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
