"""Local, isolated regression check for the AVE-57 public-site prototype."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"
OUT = ROOT / ".local" / "site-review"
URL = "http://127.0.0.1:4180"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ready() -> bool:
    try:
        with urlopen(URL, timeout=.3) as response:
            return response.status == 200
    except OSError:
        return False


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "4180", "--bind", "127.0.0.1", "--directory", str(SITE)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    try:
        for _ in range(50):
            if ready():
                break
            time.sleep(.1)
        else:
            raise RuntimeError("Public-site preview did not start")

        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="msedge")
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(URL)
            expect(page.get_by_role("heading", name="Помнит важное. Помогает доводить дела до конца.")).to_be_visible()
            expect(page.locator(".hero-visual img")).to_be_visible()
            page.screenshot(path=str(OUT / "site-desktop-dark.png"), full_page=True)

            page.get_by_role("button", name="Включить светлую тему").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "light")
            page.screenshot(path=str(OUT / "site-desktop-light.png"), full_page=True)

            page.get_by_role("button", name="Скачать", exact=True).first.click()
            expect(page.get_by_role("dialog")).to_contain_text("Публичная загрузка ещё не открыта")
            page.get_by_role("button", name="Windows", exact=True).click()
            expect(page.get_by_role("button", name="Windows", exact=True)).to_have_class("is-selected")
            page.get_by_role("button", name="Понятно", exact=True).click()
            page.get_by_role("button", name="Войти / Зарегистрироваться", exact=True).first.click()
            expect(page.get_by_role("dialog")).to_contain_text("не создаёт аккаунт")
            page.get_by_role("button", name="Понятно", exact=True).click()

            page.get_by_role("button", name="Switch to English").click()
            expect(page.get_by_role("heading", name="Remembers what matters. Helps you finish the work.")).to_be_visible()
            expect(page.locator("html")).to_have_attribute("lang", "en")

            for width, height in [(1440, 1000), (1280, 800), (768, 1024), (390, 844), (360, 800), (320, 568)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}px"
                expect(page.get_by_role("heading", name="Remembers what matters. Helps you finish the work.")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Открыть меню").click()
            expect(page.locator("#mobile-menu")).to_be_visible()
            page.locator("#mobile-menu").get_by_role("link", name="Memory").click()
            expect(page.locator("#mobile-menu")).not_to_be_visible()
            page.goto(URL)
            page.screenshot(path=str(OUT / "site-mobile-light.png"), full_page=True)

            for path in ["privacy.html", "terms.html"]:
                page.goto(f"{URL}/{path}")
                expect(page.get_by_role("link", name="Вернуться")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)

    print("PASS: AVE-57 prototype, CTA fallbacks, theme/language, legal placeholders")
    print("PASS: no horizontal overflow at 320/360/390/768/1280/1440px")


if __name__ == "__main__":
    run()
