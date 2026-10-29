"""Isolated UI regression check against the production build; no real API or model calls.

Run pnpm build in frontend first, then python scripts/verify_workspace_ui.py.
Screenshots and a machine-readable result go to .local/workspace-review.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from launch_local import FRONTEND, ROOT, NO_WINDOW, fetch, load_paths

OUT = ROOT / ".local" / "workspace-review"
URL = "http://127.0.0.1:4179"
NOW = "2026-10-28T12:00:00Z"


def fixture():
    user = dict(id="test-user", username="preview", display_name="Rickey",
                email="preview@example.test", auth_provider="password")
    threads = [dict(id=f"chat-{i}", user_id=user["id"], title=title,
                    status="active", project_id=None, pinned=False, memory_enabled=True,
                    created_at=NOW, updated_at=NOW, message_count=4)
               for i, title in enumerate(["Дизайн-система Avelin", "Планы на неделю",
                                          "Подборка книг для вечера"])]
    project = dict(id="project-1", user_id=user["id"], title="Core Agent",
                   description="Развитие личного агента", status="active", shared=False,
                   role="owner", agent_mode="explicit", revision=1,
                   created_at=NOW, updated_at=NOW, chat_count=0)
    data = {key: [] for key in [
        "notes", "history", "archived_chat_threads", "deleted_chat_threads",
        "archived_projects", "deleted_projects", "tasks", "task_runs", "automations",
        "task_reports", "notifications", "action_logs", "approval_requests",
        "permission_grants", "extension_proposals", "connectors", "workspace_files"]}
    data.update(agent_name="Avelin", provider="mock", model="preview",
                user=user, active_thread=threads[0], chat_threads=threads,
                projects=[project], active_project=None,
                security={"emergency_stop": False, "updated_at": NOW})
    data["history"] = [{"role": "assistant", "content": "Продолжим работу над проектом.", "status": "complete"}]
    data["action_logs"] = [dict(id="action-1", user_id=user["id"], tool_name="create_docx",
                               status="completed", arguments={}, result="Документ готов.", created_at=NOW)]
    return data


def populate(data):
    """Long, synthetic values expose wrapping bugs hidden by empty-state fixtures."""
    title = "Подготовка_релиза_и_проверка_документов_" * 5
    data["projects"] = [dict(data["projects"][0], id=f"project-{i}", title=title) for i in range(8)]
    data["user"]["display_name"] = "ДлинноеИмяПользователяБезПробелов" * 3
    data["model"] = "provider/long-model-name-with-context-and-version-2026-10-29"
    data["tasks"] = [dict(id="task-1", user_id="test-user", description=title, status="blocked", priority=1,
                         steps=[dict(id="step-1", task_id="task-1", description=title, status="blocked", position=1)])]
    data["automations"] = [dict(id="automation-1", name=title, prompt=title, status="active",
                               schedule_type="weekly", schedule={"weekday": 0, "time_of_day": "09:00"},
                               timezone="Asia/Irkutsk", human_rule="Каждый понедельник в 09:00", budget={}, max_parallel_runs=1)]
    data["workspace_files"] = [dict(path=f"documents/{title}.docx", name=f"{title}.docx",
                                  extension=".docx", size_bytes=4096, modified_at=NOW)]
    data["connectors"] = [dict(id="test", name=title, description=title, kind="test", enabled=False, connected=False,
                              status="disconnected", scopes=[title], capabilities=[title], tools=[title], rate_limit_per_minute=10)]
    data["permission_grants"] = [dict(id="grant-1", permission=title, scope={"path": title}, status="active", expires_at=NOW, created_at=NOW)]
    return [dict(id="memory-1", kind="fact", content=title, scope="global", auto_generated=False, confidence=1,
                 status="active", consent_status="granted", sensitive=False, provenance=[], created_at=NOW, updated_at=NOW)]


def navigate(page, title):
    if page.get_by_role("button", name="Открыть навигацию").is_visible():
        page.get_by_role("button", name="Открыть навигацию").click()
    page.locator(".sidebar").get_by_role("button", name=title, exact=True).click()


def inspect_layout(page, label, failures):
    overflow = page.evaluate("""() => {
        if (document.documentElement.scrollWidth <= innerWidth) return [];
        return Array.from(document.querySelectorAll('main *')).filter(e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.right > innerWidth + 1;
        }).slice(0, 12).map(e => e.tagName + '.' + e.className);
    }""")
    if overflow:
        failures.append(f"{label}: horizontal overflow: {overflow}")
        page.screenshot(path=str(OUT / f"overflow-{label}.png"), full_page=True)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text('{"status":"running"}', encoding="utf-8")
    _, node = load_paths()
    data = fixture()
    stream_requests = []
    unexpected = []
    errors = []
    results = []
    personal_records = []
    stream_mode = "complete"
    pending_routes = []
    settings = dict(autonomous_enabled=False, health_autonomous_enabled=False, updated_at=NOW)
    def route_api(route):
        path = urlparse(route.request.url).path
        if path == "/api/auth/me":
            body = data["user"]
        elif path == "/api/bootstrap":
            target = parse_qs(urlparse(route.request.url).query).get("thread_id", [data["active_thread"]["id"]])[0]
            data["active_thread"] = next(t for t in data["chat_threads"] if t["id"] == target)
            body = data
        elif path == "/api/models":
            body = [{"provider": "mock", "label": "Тестовая модель", "models": ["preview"], "description": "Preview"}]
        elif path == "/api/memory":
            body = {"counts": {key: 0 for key in ["notes", "global_notes", "project_notes", "chat_notes", "memory_index", "chat_messages", "tasks", "action_logs", "personal_records", "health_records"]}, "notes": []}
        elif path == "/api/memory/settings":
            body = settings
        elif path == "/api/memory/summary":
            body = {"generated_at": NOW, "settings": settings, "sections": {"profile": [], "preference": [], "fact": personal_records, "episode": []}, "record_count": len(personal_records)}
        elif path == "/api/memory/records":
            body = personal_records
        elif path.endswith(("/compactions", "/members", "/invitations", "/shared-items")):
            body = []
        elif path == "/api/chats" and route.request.method == "POST":
            thread = dict(data["chat_threads"][0], id="new-thread", title="Новый диалог", message_count=0)
            data["chat_threads"].insert(0, thread)
            data["active_thread"] = thread
            data["history"] = []
            body = {"thread": thread}
        elif path == "/api/chat/stream":
            payload = route.request.post_data_json
            stream_requests.append(payload)
            if stream_mode == "hold":
                pending_routes.append(route)
                return
            if stream_mode == "error":
                route.fulfill(status=503, json={"detail": "Провайдер временно недоступен. Повтори запрос позже."})
                return
            data["history"] = [{"role": "user", "content": payload["message"], "status": "complete"},
                               {"role": "assistant", "content": "Тестовый ответ готов.", "status": "complete"}]
            events = [{"type": "delta", "thread_id": payload["thread_id"], "text": "Тестовый ответ готов."},
                      {"type": "done", "thread_id": payload["thread_id"]}]
            route.fulfill(status=200, content_type="application/x-ndjson", body="\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
            return
        elif path.startswith("/api/workspace/files/"):
            route.fulfill(status=200, content_type="application/octet-stream", body="Synthetic download fixture")
            return
        else:
            unexpected.append(path)
            route.fulfill(status=404, json={"detail": "Unmocked API"})
            return
        route.fulfill(status=200, json=body)

    with (OUT / "preview.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([str(node), str(FRONTEND / "node_modules/vite/bin/vite.js"),
                                   "preview", "--host", "127.0.0.1", "--port", "4179", "--strictPort"],
                                  cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        try:
            for _ in range(60):
                if server.poll() is not None:
                    raise RuntimeError("Preview server exited; inspect preview.log")
                if fetch(URL, timeout=.5):
                    break
                time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel="msedge")
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
                context.add_init_script("localStorage.setItem('avelin-auth-token','fixture-token'); localStorage.setItem('avelin-theme-mode','light');")
                context.route("**/api/**", route_api)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(URL)
                expect(page.get_by_role("heading", name="На чём продолжим?")).to_be_visible()
                expect(page.locator(".recent-card")).to_have_count(3)
                assert page.locator(".sidebar .panel").count() == 0
                assert page.locator(".sidebar").evaluate("(e) => e.scrollHeight <= e.clientHeight + 1")
                page.screenshot(path=str(OUT / "workspace-desktop-light.png"), full_page=True)
                results.append("desktop home: compact navigation and real-data cards")
                for width, height, label in [(1152, 800, "125"), (960, 667, "150"), (390, 844, "mobile")]:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), label
                    page.screenshot(path=str(OUT / f"workspace-{label}-light.png"), full_page=True)
                results.append("1440/1152/960/390px: no horizontal overflow (viewport equivalents, not OS DPI)")
                page.get_by_role("button", name="Открыть навигацию").click()
                expect(page.get_by_role("button", name="Главная", exact=True)).to_be_visible()
                assert page.locator("main").evaluate("e => e.inert")
                page.keyboard.press("Shift+Tab")
                assert page.locator(".sidebar").evaluate("e => e.contains(document.activeElement)")
                page.keyboard.press("Tab")
                expect(page.locator(".nav-close")).to_be_focused()
                page.keyboard.press("Escape")
                expect(page.get_by_role("button", name="Открыть навигацию")).to_be_focused()
                page.get_by_role("button", name="Открыть навигацию").click()
                page.get_by_role("button", name="Память", exact=True).click()
                expect(page.get_by_role("heading", name="Память", exact=True)).to_be_visible()
                expect(page.locator(".sidebar")).not_to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                results.append("mobile menu: open, Escape, focus return, navigation and close")
                page.set_viewport_size({"width": 1440, "height": 1000})
                for title in ["Проекты", "Память", "Автоматизации", "Задачи", "Файлы", "Настройки"]:
                    page.locator(".sidebar").get_by_role("button", name=title, exact=True).click()
                    expect(page.locator(".page-heading")).to_contain_text(title)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), title
                for title in ["Подключения", "Инструменты", "Разрешения", "Журнал", "Общие"]:
                    page.get_by_role("navigation", name="Разделы настроек").get_by_role("button", name=title, exact=True).click()
                    expect(page.locator(".settings-content .panel").first).to_be_visible()
                page.screenshot(path=str(OUT / "workspace-settings.png"), full_page=True)
                results.append("all main screens and five settings tabs render")
                page.locator(".sidebar").get_by_role("button", name="Главная", exact=True).click()
                page.get_by_role("button", name="Включить тёмную тему").click()
                expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                page.screenshot(path=str(OUT / "workspace-desktop-dark.png"), full_page=True)
                results.append("dark theme")
                page.locator(".recent-card").first.click()
                expect(page.locator(".chat-card")).to_be_visible()
                page.get_by_role("textbox", name="Поиск диалогов").fill("книг")
                expect(page.locator(".chat-list-item")).to_have_count(1)
                page.locator(".chat-list-item").click()
                expect(page.locator(".chat-header")).to_contain_text("Подборка книг")
                page.screenshot(path=str(OUT / "workspace-chat.png"), full_page=True)
                results.append("recent card opens saved chat; search and selection work")
                page.locator(".sidebar").get_by_role("button", name="Главная", exact=True).click()
                page.get_by_role("textbox", name="Новая задача").fill("Тест нового диалога")
                page.get_by_role("button", name="Начать диалог", exact=True).click()
                expect(page.locator(".message-assistant")).to_contain_text("Тестовый ответ готов.")
                assert len(stream_requests) == 1 and stream_requests[0]["thread_id"] == "new-thread"
                results.append("home prompt creates a new chat and streams to its ID")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(OUT / "workspace-mobile-chat.png"), full_page=True)
                page.locator(".mobile-chat-toggle").click()
                expect(page.locator(".chat-browser")).to_be_visible()
                expect(page.locator(".chat-card")).not_to_be_visible()
                page.get_by_role("textbox", name="Поиск диалогов").fill("Несуществующий диалог")
                expect(page.locator(".chat-search-empty")).to_be_visible()
                page.get_by_role("textbox", name="Поиск диалогов").fill("")
                page.locator(".chat-list-item").first.click()
                expect(page.locator(".chat-card")).to_be_visible()
                page.get_by_role("button", name="Открыть навигацию").click()
                page.set_viewport_size({"width": 1440, "height": 900})
                expect(page.locator(".sidebar-open")).to_have_count(0)
                assert not page.locator("main").evaluate("e => e.inert")
                assert page.evaluate("document.body.style.overflow !== 'hidden'")
                results.append("mobile chat list/search and drawer focus isolation; desktop resize unlocks page")
                personal_records = populate(data)
                page.reload()
                expect(page.locator(".recent-card").first).to_be_visible()
                failures = []
                for theme in ["light", "dark"]:
                    navigate(page, "Настройки")
                    page.get_by_role("button", name="Светлая" if theme == "light" else "Тёмная", exact=True).click()
                    for width, height in [(320, 568), (390, 844), (768, 1024), (1024, 768), (1440, 900)]:
                        page.set_viewport_size({"width": width, "height": height})
                        for title in ["Главная", "Проекты", "Память", "Автоматизации", "Задачи", "Файлы", "Настройки"]:
                            navigate(page, title)
                            inspect_layout(page, f"{theme}-{width}-{title}", failures)
                        for title in ["Подключения", "Инструменты", "Разрешения", "Журнал", "Общие"]:
                            page.get_by_role("navigation", name="Разделы настроек").get_by_role("button", name=title, exact=True).click()
                            inspect_layout(page, f"{theme}-{width}-settings-{title}", failures)
                            if width == 390 and title == "Разрешения":
                                page.screenshot(path=str(OUT / f"workspace-mobile-permissions-{theme}.png"), full_page=True)
                results.append("populated screens/settings: 320/390/768/1024/1440px in both themes (120 layout checks)")

                long_name = "Очень_длинное_название_итогового_документа_без_пробелов_" * 4
                data["history"] = [
                    {"role": "user", "content": "Проверь длинный ответ и документ.", "status": "complete"},
                    {"role": "assistant", "status": "complete", "content":
                     "## Проверка содержимого\n\n" + "Абзац с подробностями.\n\n" * 30 +
                     "| " + " | ".join(["Заголовок таблицы"] * 8) + " |\n|" + "---|" * 8 + "\n|" + "данные|" * 8 + "\n\n" +
                     "```python\n" + "long_variable_name_" * 60 + "\n```\n\n" +
                     "https://example.test/" + "long-url-" * 50,
                     "artifacts": [{"path": "test.docx", "name": long_name + ".docx", "extension": ".docx", "size_bytes": 4096}]}
                ]
                data["active_thread"]["title"] = long_name
                page.reload()
                expect(page.locator(".recent-card").first).to_be_visible()
                page.locator(".recent-card").first.click()
                expect(page.locator(".message-artifacts")).to_be_visible()
                for width, height in [(1440, 900), (960, 667), (390, 844), (320, 568), (844, 390), (390, 420)]:
                    page.set_viewport_size({"width": width, "height": height})
                    inspect_layout(page, f"{width}x{height}-long-chat", failures)
                    if width > 600:
                        expect(page.locator(".mobile-chat-toggle")).not_to_be_visible()
                        assert page.locator(".chat-card").bounding_box()["x"] > page.locator(".chat-browser").bounding_box()["x"]
                    if not page.locator(".composer").evaluate("e => e.getBoundingClientRect().bottom <= innerHeight + 1"):
                        failures.append(f"{width}x{height}: composer falls below viewport")
                    page.screenshot(path=str(OUT / f"chat-stress-{width}x{height}.png"), full_page=True)
                table = page.locator(".markdown-table")
                assert table.evaluate("e => e.scrollWidth > e.clientWidth")
                assert page.locator("th").first.evaluate("e => e.getBoundingClientRect().width >= 120")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator(".messages").evaluate("e => e.scrollTop = e.scrollHeight")
                with page.expect_download() as download:
                    page.locator(".message-artifacts .artifact-card").click()
                assert download.value.suggested_filename.endswith(".docx")
                navigate(page, "Файлы")
                with page.expect_download() as file_download:
                    page.locator(".workspace-file-list .artifact-card").click()
                assert file_download.value.suggested_filename.endswith(".docx")
                results.append("artifact downloads from chat and Files screen (mocked file endpoint)")

                navigate(page, "Диалоги")
                page.locator(".chat-list-item").first.click()
                stream_mode = "hold"
                page.get_by_role("textbox", name="Сообщение Avelin").fill("Проверка состояния ожидания")
                page.get_by_role("button", name="Отправить", exact=True).click()
                expect(page.get_by_role("status", name="Avelin печатает")).to_be_visible()
                expect(page.get_by_role("button", name="Остановить", exact=True)).to_be_in_viewport()
                page.get_by_role("button", name="Остановить", exact=True).click()
                expect(page.locator(".message-cancelled")).to_be_visible()
                for route in pending_routes:
                    route.abort()
                expect(page.get_by_role("button", name="Отправить", exact=True)).to_be_visible()
                page.get_by_role("button", name="Закрыть сообщение об ошибке").click()
                stream_mode = "error"
                page.get_by_role("textbox", name="Сообщение Avelin").fill("Проверка ошибки провайдера")
                page.get_by_role("button", name="Отправить", exact=True).click()
                expect(page.locator(".workspace-error")).to_contain_text("Провайдер временно недоступен")
                expect(page.locator(".message-failed")).to_be_visible()
                inspect_layout(page, "mobile-provider-error", failures)
                assert page.locator(".composer").evaluate("e => e.getBoundingClientRect().bottom <= innerHeight + 1")
                page.screenshot(path=str(OUT / "workspace-mobile-error.png"), full_page=True)
                results.append("mobile waiting/cancel/error states keep controls visible and usable")

                auth_context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                auth_page = auth_context.new_page()
                auth_page.on("pageerror", lambda error: errors.append(str(error)))
                auth_page.goto(URL)
                for width in [1440, 768, 390, 320]:
                    auth_page.set_viewport_size({"width": width, "height": 900})
                    for title in ["Вход", "Регистрация"]:
                        auth_page.get_by_role("button", name=title, exact=True).click()
                        inspect_layout(auth_page, f"auth-{width}-{title}", failures)
                auth_page.set_viewport_size({"width": 390, "height": 844})
                auth_page.screenshot(path=str(OUT / "workspace-mobile-register.png"), full_page=True)
                auth_context.close()
                results.append("sign-in and registration layouts at 320/390/768/1440px; no account created")
                (OUT / "layout-failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
                assert not failures, "\n".join(failures)
                results.append("long chat, Markdown table/code/link and artifact; composer fits short viewports")
                assert not errors, errors
                assert not unexpected, unexpected
                results.append("no JavaScript errors or unexpected API calls")
                browser.close()
        except Exception as error:
            (OUT / "results.json").write_text(json.dumps({"status": "failed", "error": str(error), "completed_checks": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
    (OUT / "results.json").write_text(json.dumps({"status": "passed", "checks": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print("PASS:", result)


if __name__ == "__main__":
    run()
