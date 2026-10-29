"""Local, isolated regression check for the AVE-57/58 public site."""
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

            page.evaluate("window.ctaEvents=[]; window.addEventListener('avelin:cta', event => window.ctaEvents.push(event.detail))")
            page.get_by_role("button", name="Скачать", exact=True).first.click()
            expect(page.get_by_role("dialog")).to_contain_text("Выбери платформу")
            windows = page.locator('[data-platform="windows"]')
            expect(windows).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#platform-note")).to_contain_text("Windows: релиз ещё готовится")
            windows_box = windows.bounding_box()
            macos_box = page.locator('[data-platform="macos"]').bounding_box()
            assert windows_box and macos_box and windows_box["y"] == macos_box["y"], "desktop platform picker is not a two-column grid"
            page.locator('[data-platform="android"]').click()
            expect(page.locator('[data-platform="android"]')).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#dialog-action")).to_have_attribute("href", "./download.html")
            page.screenshot(path=str(OUT / "site-download-dialog.png"))
            assert page.evaluate("Object.keys(window.ctaEvents.at(-1)).sort().join(',')") == "available,name,platform"
            assert page.evaluate("AvelinSite.safeReturnPath('/projects/42?tab=chat#last')") == "/projects/42?tab=chat#last"
            assert page.evaluate("AvelinSite.safeReturnPath('https://evil.example/steal')") == "/"
            assert page.evaluate("AvelinSite.safeReturnPath('//evil.example/steal')") == "/"
            assert page.evaluate("AvelinSite.safeConfiguredUrl('javascript:alert(1)', './download.html', true).endsWith('/download.html')")
            page.locator(".dialog-ok").click()
            page.get_by_role("button", name="Войти / Зарегистрироваться", exact=True).first.click()
            expect(page.get_by_role("dialog")).to_contain_text("не создаёт аккаунт")
            page.locator(".dialog-ok").click()

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

            for path in ["privacy.html", "terms.html", "download.html"]:
                page.goto(f"{URL}/{path}")
                expect(page.get_by_role("link", name="Вернуться")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

            device_cases = [
                ("ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", 390, 844),
                ("ios", "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", 768, 1024),
                ("android", "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36", 360, 800),
                ("macos", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36", 1280, 800),
            ]
            for expected_platform, user_agent, width, height in device_cases:
                context = browser.new_context(user_agent=user_agent, viewport={"width": width, "height": height})
                device_page = context.new_page()
                device_page.goto(URL)
                assert device_page.evaluate("AvelinSite.detectPlatform()") == expected_platform
                device_page.get_by_role("button", name="Скачать", exact=True).first.click()
                expect(device_page.locator(f'[data-platform="{expected_platform}"]')).to_have_attribute("aria-pressed", "true")
                assert device_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if expected_platform == "ios" and width == 390:
                    device_page.screenshot(path=str(OUT / "site-download-iphone.png"))
                context.close()
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)

    print("PASS: AVE-57 presentation, themes/languages, legal and download fallbacks")
    print("PASS: AVE-58 Windows/macOS/iPhone/iPad/Android routing and explicit override")
    print("PASS: same-origin auth return guard and privacy-minimal CTA event payload")
    print("PASS: no horizontal overflow at 320/360/390/768/1280/1440px")


if __name__ == "__main__":
    run()
