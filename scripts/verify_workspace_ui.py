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
from urllib.parse import parse_qs, urlparse, unquote

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
    model_requests = []
    chat_models = {}
    attachment_requests = []
    approval_decisions = []
    automation_starts = []
    chat_creations = []
    chat_creation_payloads = []
    project_creations = []
    chat_plans = []
    plan_decisions = []
    avatar_state = {"bytes": None}
    preview_authorizations = []
    push_requests = []
    push_enabled = False
    legacy_runtime = False
    png_bytes = (FRONTEND / "public" / "brand" / "icon-192.png").read_bytes()
    unexpected = []
    errors = []
    results = []
    personal_records = []
    stream_mode = "complete"
    reject_model = False
    pending_routes = []
    chat_runs, request_runs, latest_runs = {}, {}, {}
    cancelled_runs = []
    api_requests = []
    settings = dict(autonomous_enabled=False, health_autonomous_enabled=False, updated_at=NOW)
    def public_run(item):
        return None if item is None else {key:value for key,value in item.items() if not key.startswith('_')}
    def make_run(payload, reply=None):
        existing = request_runs.get(payload['request_id'])
        if existing: return chat_runs[existing]
        identifier = f'fixture-run-{len(chat_runs)+1}'
        user = next((item for item in reversed(data['history']) if item['role']=='user'),None) if reply else None
        item = {'run_id':identifier,'request_id':payload['request_id'],'thread_id':payload['thread_id'],'state':'running','text':'','latest_seq':0,'message_id':identifier+'-assistant','user_message_id':user.get('id') if user else identifier+'-user','user_text':user['content'] if user else payload['message'],'attachments':payload.get('attachments',[]),'artifacts':[],'approvals':[], 'cancel_requested':False,'_mode':'continuation' if reply else stream_mode,'_reply':reply,'_events':None}
        if not reply:
            data['history'] = [{'id':item['user_message_id'],'role':'user','content':payload['message'],'status':'complete','artifacts':[{'path':path,'name':'photo.png' if path.endswith('.png') else 'test-note.txt','extension':'.png' if path.endswith('.png') else '.txt','size_bytes':24} for path in payload.get('attachments',[])]}]
        data['history'].append({'id':item['message_id'],'role':'assistant','content':'','status':'pending'})
        chat_runs[identifier] = item; request_runs[payload['request_id']] = identifier; latest_runs[payload['thread_id']] = identifier
        return item
    def run_events(item):
        if item['_events'] is not None: return item['_events']
        mode = item['_mode']; thread = item['thread_id']
        if mode == 'truncated':
            item['text'] = 'Частичный текст'
            events = [{'type':'delta','text':item['text']}]
        elif mode == 'approval':
            approval = {'id':'approval-fixture','thread_id':thread,'tool_name':'http_request','risk':'external','scope':{'host':'example.test'},'arguments':{'url':'https://example.test','Authorization':'[REDACTED]'},'summary':'Отправить запрос на example.test','status':'pending','expires_at':NOW}
            item['approvals'] = [approval]; data['chat_approvals'] = [approval]
            item['state'] = 'waiting_approval';item['text']='Нужно подтверждение действия.'
            events = [{'type':'approval','approval':approval},{'type':'delta','text':item['text']},{'type':'done','state':'waiting_approval','outcome':'waiting_approval'}]
        else:
            item['text'] = item['_reply'] or 'Тестовый ответ готов.';item['state']='completed'
            events = [{'type':'status','text':'Продолжаю после решения' if item['_reply'] else 'Читаю файл…','tool':'read_file','phase':'started'},{'type':'status','text':'Файл прочитан','tool':'read_file','phase':'completed'},{'type':'delta','text':item['text']},{'type':'done','state':'completed','outcome':'completed'}]
        for seq,event in enumerate(events,1): event.update(seq=seq,run_id=item['run_id'],thread_id=thread)
        item['latest_seq']=len(events);item['_events']=events
        row=next((row for row in data['history'] if row.get('id')==item['message_id']),None)
        if row: row.update(content=item['text'],status='pending' if mode=='truncated' else 'complete')
        next((thread for thread in data['chat_threads'] if thread['id']==item['thread_id']),data['active_thread'])['message_count']=len(data['history'])
        return events
    def route_api(route):
        path = urlparse(route.request.url).path
        api_requests.append(route.request.method + ' ' + route.request.url)
        if path == "/api/health":
            body = {"status":"ok"} if legacy_runtime else {"status":"ok","runtime":{"contract":2},"restart_required":False}
        elif path == "/api/auth/me":
            body = data["user"]
        elif path == "/api/auth/registration-policy":
            body = {"access_code_required": False, "message": ""}
        elif path == "/api/profile/avatar":
            if route.request.method == "GET":
                route.fulfill(status=200 if avatar_state["bytes"] else 404, content_type="image/png", body=avatar_state["bytes"] or b"")
                return
            if route.request.method == "DELETE":
                avatar_state["bytes"] = None
            else:
                avatar_state["bytes"] = route.request.post_data_buffer
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(avatar_state["bytes"])) as avatar:
                    assert avatar.size == (256,256)
            body = {"updated_at":NOW,"width":256,"height":256,"mime_type":"image/png"}
        elif path == "/api/profile/avatar/preview":
            route.fulfill(status=200,content_type="image/png",body=png_bytes)
            return
        elif path == "/api/workspace/preview":
            preview_authorizations.append(route.request.headers.get("authorization"))
            route.fulfill(status=200,content_type="image/png",body=png_bytes)
            return
        elif path == "/api/notifications":
            body = [item for item in data.get("notifications", []) if not item.get("read_at")]
        elif path == "/api/notifications/push/config":
            body = {"enabled":push_enabled,"public_key":"BC" + "A" * 85 if push_enabled else None,"reason":"Тестовый сервер не отправляет push."}
        elif path.startswith("/api/notifications/push/"):
            push_requests.append({"path":path,"method":route.request.method,"body":route.request.post_data_json,"authorization":route.request.headers.get("authorization")})
            body = {"state":"pending"} if "/push/test/" in path else {"ok":True,"queued":1,"state":"pending","subscription_id":"push-fixture"}
        elif path == "/api/auth/logout":
            body = {"ok":True}
        elif path == "/api/bootstrap":
            target = parse_qs(urlparse(route.request.url).query).get("thread_id", [data["active_thread"]["id"]])[0]
            selected_thread = next((t for t in data["chat_threads"] if t["id"] == target),None)
            if selected_thread is None:
                route.fulfill(status=404,json={"detail":"Диалог недоступен"})
                return
            data["active_thread"] = selected_thread
            body = data
        elif path == "/api/models":
            body = [{"provider": "mock", "label": "Тестовая модель", "models": ["preview", "preview-alternative"], "description": "Preview"}]
        elif path == "/api/home/suggestions":
            body = {"basic":[],"advanced":[],"personal":[],"personalization_enabled":False}
        elif path.startswith("/api/chats/") and path.endswith("/model-settings"):
            thread = path.split("/")[3]
            if route.request.method == "PUT":
                payload = route.request.post_data_json
                model_requests.append(payload)
                if reject_model:
                    route.fulfill(status=503,json={"detail":"Модель временно недоступна"});return
                chat_models[thread] = {**payload,"override":True}
            elif route.request.method == "DELETE": chat_models.pop(thread,None)
            body = chat_models.get(thread,{"provider":data["provider"],"model_name":data["model"],"ollama_url":"","override":False})
        elif path.endswith("/media"):
            body = {"thread_id":path.split("/")[3],"images":[],"files":[],"links":[],"next_before":None}
        elif path == "/api/model-settings":
            payload = route.request.post_data_json
            model_requests.append(payload)
            if reject_model:
                route.fulfill(status=503, json={"detail": "Модель временно недоступна"})
                return
            data["provider"] = payload["provider"]
            data["model"] = payload["model_name"]
            body = payload
        elif path == "/api/attachments":
            attachment_requests.append(route.request.headers.get("x-filename", ""))
            filename = unquote(route.request.headers.get("x-filename", ""))
            body = {"path": "uploads/photo.png" if filename.endswith(".png") else "uploads/fixture.txt", "name":filename, "mime_type": "image/png" if filename.endswith(".png") else "text/plain", "size_bytes":len(png_bytes) if filename.endswith(".png") else 24}
        elif path == "/api/memory":
            body = {"counts": {key: 0 for key in ["notes", "global_notes", "project_notes", "chat_notes", "memory_index", "chat_messages", "tasks", "action_logs", "personal_records", "health_records"]}, "notes": []}
        elif path == "/api/memory/settings":
            body = settings
        elif path == "/api/memory/summary":
            body = {"generated_at": NOW if personal_records else "", "status":"ready" if personal_records else "empty" if settings["autonomous_enabled"] else "disabled", "method":"structured_records", "explanation":"Сводка группирует сохранённые факты и не перечитывает все чаты.", "autonomous_record_count":0,"manual_record_count":len(personal_records), "settings": settings, "sections": {"profile": [], "preference": [], "fact": personal_records, "episode": []}, "record_count": len(personal_records)}
        elif path == "/api/memory/records":
            body = personal_records
        elif path == "/api/projects" and route.request.method == "POST":
            payload = route.request.post_data_json
            project = dict(data["projects"][0], id="shared-group" if payload.get("shared") else f"private-project-{len(project_creations)+1}", title=payload["title"], description=payload.get("description", ""), shared=payload.get("shared", False), agent_mode="mention_only", role="owner")
            project_creations.append(project["id"])
            data["projects"].append(project)
            body = {"project": project}
        elif path.startswith("/api/projects/") and route.request.method == "PATCH":
            project = next(item for item in data["projects"] if item["id"] == path.split("/")[3])
            project.update({key:value for key,value in route.request.post_data_json.items() if key in {"title","description"}})
            project["revision"] += 1
            body = {"project":project}
        elif path.endswith("/invitations") and route.request.method == "POST":
            body = {"id":"invite-test", "project_id":"shared-group", "email":route.request.post_data_json.get("email", ""), "role":"editor", "status":"pending", "expires_at":NOW, "created_at":NOW, "token":"test-invite-token"}
        elif path.startswith("/api/automations/") and path.endswith("/runs"):
            automation = next((item for item in data["automations"] if item["id"] in path), None)
            body = [automation["last_run"]] if automation and automation.get("last_run") else []
        elif path.startswith("/api/automations/") and path.endswith("/run"):
            automation_starts.append(path)
            automation = next(item for item in data["automations"] if item["id"] in path)
            run = {"id":"manual-run", "automation_id":automation["id"], "scheduled_for":NOW,"task_id":"manual-task", "task_run_id":"manual-task-run", "state":"completed","budget":{},"usage":{},"created_at":NOW,"finished_at":NOW,"result":"Автоматизация подготовила документ.","artifacts":[],"thread_id":data["chat_threads"][0]["id"],"outcome":"completed","approval_ids":[]}
            automation["last_run"] = run
            body = {**run,"state":"queued"}
        elif "/approvals/" in path and path.endswith("/runs"):
            incoming = route.request.post_data_json
            decision = incoming["decision"]; approval_decisions.append(decision)
            data["chat_approvals"] = []
            reply = "Разрешённое действие завершено." if decision == "approved" else "Действие отклонено."
            body = public_run(make_run({"request_id":incoming["request_id"],"thread_id":path.split("/")[3],"message":""},reply))
        elif path.startswith("/api/long-tasks/") and path.endswith("/decision"):
            payload = route.request.post_data_json
            plan_decisions.append(payload)
            chat_plans[0]["status"] = "approved" if payload["decision"] == "approved" else "rejected"
            chat_plans[0]["run"] = {"state":"queued","usage":{}}
            body = chat_plans[0]
        elif path.endswith("/plans"):
            body = chat_plans
        elif path.endswith(("/compactions", "/members", "/invitations", "/shared-items", "/plans")):
            body = []
        elif path == "/api/chats" and route.request.method == "POST":
            payload = route.request.post_data_json
            chat_creation_payloads.append(payload)
            shared = any(project["id"] == payload.get("project_id") and project.get("shared") for project in data["projects"])
            thread = next((item for item in data["chat_threads"] if payload.get("reuse_empty") and not payload.get("title") and not shared and item.get("project_id") == payload.get("project_id") and item["message_count"] == 0 and item["title"] in {"Новый диалог", "New chat"} and not item.get("pinned")), None)
            if thread is None:
                thread = dict(data["chat_threads"][0], id="group-thread" if shared else f"project-thread-{len(chat_creations)+1}" if payload.get("project_id") else "new-thread" if not chat_creations else f"new-thread-{len(chat_creations)+1}", project_id=payload.get("project_id"), title=payload.get("title") or "Новый диалог", message_count=0)
                data["chat_threads"].insert(0, thread)
                chat_creations.append(thread["id"])
            data["active_thread"] = thread
            data["history"] = []
            body = {"thread": thread}
        elif path == "/api/chat/runs" and route.request.method == "POST":
            payload = route.request.post_data_json; stream_requests.append(payload)
            if stream_mode == "error":
                route.fulfill(status=503,json={"detail":"Провайдер временно недоступен. Повтори запрос позже."});return
            body = public_run(make_run(payload))
        elif path.startswith("/api/chat/runs/by-request/"):
            body = public_run(chat_runs.get(request_runs.get(path.rsplit("/",1)[1])))
        elif path.endswith("/runs/active"):
            body = public_run(chat_runs.get(latest_runs.get(path.split("/")[3])))
        elif path.startswith("/api/chat/runs/"):
            item = chat_runs[path.split("/")[4]]
            if path.endswith("/events"):
                if item["_mode"] == "hold" and item["state"] == "running":
                    pending_routes.append(route);return
                after = int(parse_qs(urlparse(route.request.url).query).get("after",["0"])[0])
                events = [event for event in run_events(item) if event["seq"] > after]
                route.fulfill(content_type="application/x-ndjson",body="".join(json.dumps(event,ensure_ascii=False)+"\n" for event in events));return
            if path.endswith("/cancel"):
                cancelled_runs.append(item["run_id"]);item["state"]="cancelled";item["cancel_requested"]=True
                row=next((row for row in data["history"] if row.get("id")==item["message_id"]),None)
                if row:row.update(content=item["text"] or "Генерация остановлена.",status="cancelled")
            body = public_run(item)
        elif path.startswith("/api/workspace/files/"):
            route.fulfill(status=200, content_type="application/octet-stream", body="Synthetic download fixture")
            return
        else:
            unexpected.append(path)
            route.fulfill(status=404, json={"detail": "Unmocked API"})
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body,ensure_ascii=False))

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
                context.grant_permissions(["clipboard-read", "clipboard-write"],origin=URL)
                context.add_init_script("localStorage.setItem('avelin-auth-token','fixture-token'); localStorage.setItem('avelin-theme-mode','light');")
                context.route("**/api/**", route_api)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(URL + "/?view=home")
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
                expect(page.get_by_role("button", name="Обновить список",exact=True)).to_be_visible()
                expect(page.locator(".memory-summary-card")).to_contain_text("Автосбор выключен")
                expect(page.locator(".memory-summary-card")).to_contain_text("не перечитывает все чаты")
                expect(page.locator(".sidebar")).not_to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                results.append("mobile menu: open, Escape, focus return, navigation and close")
                page.set_viewport_size({"width": 1440, "height": 1000})
                for title in ["Совместно", "Проекты", "Память", "Автоматизации", "Задачи", "Файлы", "Настройки"]:
                    page.locator(".sidebar").get_by_role("button", name=title, exact=True).click()
                    expect(page.locator(".page-heading")).to_contain_text(title)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), title
                for title in ["Подключения", "Инструменты", "Разрешения", "Журнал", "Общие"]:
                    page.get_by_role("navigation", name="Разделы настроек").get_by_role("button", name=title, exact=True).click()
                    expect(page.locator(".settings-content .panel").first).to_be_visible()
                page.screenshot(path=str(OUT / "workspace-settings.png"), full_page=True)
                page.locator('input[aria-label="Выбрать фото профиля"]').set_input_files({"name":"portrait.png","mimeType":"image/png","buffer":png_bytes})
                avatar_dialog = page.get_by_role("dialog", name="Фото профиля",exact=True)
                expect(avatar_dialog).to_be_visible()
                avatar_dialog.get_by_role("slider",name="Масштаб фото").focus()
                avatar_dialog.get_by_role("slider",name="Масштаб фото").press("ArrowRight")
                page.screenshot(path=str(OUT / "workspace-mobile-avatar-crop.png"))
                avatar_dialog.get_by_role("button",name="Сохранить фото",exact=True).click()
                expect(avatar_dialog).not_to_be_visible()
                expect(page.locator(".nav-profile .avatar img")).to_be_visible()
                assert avatar_state["bytes"]
                page.get_by_role("button",name="Удалить фото",exact=True).click()
                expect(page.locator(".nav-profile .avatar img")).to_have_count(0)
                results.append("profile photo crop saves a 256px image, updates sidebar and can be removed")
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
                picker = page.get_by_role("combobox", name="Модель для диалога")
                with page.expect_response(lambda response: urlparse(response.url).path.endswith("/model-settings") and response.request.method == "PUT"):
                    picker.select_option(label="preview-alternative")
                expect(picker).to_have_value(json.dumps(["mock", "preview-alternative"], separators=(",", ":")))
                expect(picker).to_be_enabled()
                assert model_requests[-1]["model_name"] == "preview-alternative"
                reject_model = True
                picker.select_option(label="preview")
                expect(page.locator(".workspace-error")).to_contain_text("Модель временно недоступна")
                expect(picker).to_have_value(json.dumps(["mock", "preview-alternative"], separators=(",", ":")))
                reject_model = False
                page.get_by_role("button", name="Закрыть сообщение об ошибке").click()
                assert page.get_by_role("button", name="Сохранить", exact=True).count() == 0
                results.append("chat model selector auto-saves and rolls back on API failure without Save")
                for _ in range(2):
                    page.get_by_role("button", name="Новый диалог",exact=True).click()
                    expect(page.locator(".chat-header")).to_contain_text("Новый диалог")
                    expect(page.get_by_role("button", name="Новый диалог",exact=True)).to_be_enabled()
                assert chat_creations == ["new-thread"]
                seed = data["chat_threads"][0]
                data["chat_threads"].extend([
                    dict(seed,id="legacy-empty",title="New chat",message_count=0,pinned=False),
                    dict(seed,id=data["user"]["id"],user_id=data["user"]["id"],title="Main conversation",message_count=0,pinned=False),
                    dict(seed,id="named-empty",title="План без сообщений",message_count=0,pinned=False),
                    dict(seed,id="pinned-empty",title="New chat",message_count=0,pinned=True),
                ])
                page.reload()
                navigate(page, "Главная")
                expect(page.locator(".recent-card")).to_have_count(3)
                navigate(page,"Диалоги")
                expect(page.locator(".chat-list-item")).to_have_count(6)
                expect(page.locator(".chat-list-item").filter(has_text="Main conversation")).to_have_count(0)
                expect(page.locator(".chat-list-item").filter(has_text="План без сообщений")).to_have_count(1)
                expect(page.locator(".chat-list-item").filter(has_text="New chat")).to_have_count(1)
                page.locator(".sidebar").get_by_role("button", name="Главная", exact=True).click()
                expect(page.locator(".recent-card")).to_have_count(3)
                assert not any("Новый диалог" in title for title in page.locator(".recent-card h4").all_text_contents())
                results.append("repeated New reuses one draft; stale automatic empties hidden from history, named/pinned/current preserved")
                page.locator('input[type="file"]').set_input_files({"name": "test-note.txt", "mimeType": "text/plain", "buffer": b"Synthetic attachment data"})
                expect(page.locator(".attachment-chip")).to_contain_text("test-note.txt")
                page.get_by_role("textbox", name="Новая задача").fill("Тест нового диалога")
                page.get_by_role("button", name="Начать диалог", exact=True).click()
                expect(page.locator(".message-assistant")).to_contain_text("Тестовый ответ готов.")
                assert len(stream_requests) == 1 and stream_requests[0]["thread_id"] == "new-thread"
                expect(page.locator('.chat-list-item[aria-current="true"]')).to_contain_text("2 сообщений")
                assert stream_requests[0]["attachments"] == ["uploads/fixture.txt"]
                assert attachment_requests == ["test-note.txt"]
                expect(page.locator(".attachment-chip")).to_have_count(0)
                expect(page.locator(".message-user .artifact-card")).to_contain_text("test-note.txt")
                expect(page.locator(".generation-activity summary")).to_have_text("Подробности")
                page.locator(".generation-activity summary").click()
                expect(page.get_by_role("list", name="Действия Avelin")).to_contain_text("Файл прочитан")
                results.append("file upload sends owned attachment path and displays actual stream actions")
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
                expect(page.locator(".message-role")).to_have_count(0)
                page.locator(".sidebar").get_by_role("button", name="Совместно", exact=True).click()
                expect(page.get_by_role("heading", name="Совместно", exact=True)).to_be_visible()
                page.get_by_role("button", name="Новая группа", exact=True).click()
                group_dialog = page.get_by_role("dialog", name="Новая группа", exact=True)
                expect(group_dialog).to_be_visible()
                group_dialog.get_by_label("Название", exact=True).fill("Команда дизайна")
                group_dialog.get_by_role("button", name="Создать группу", exact=True).click()
                expect(page.locator(".chat-header")).to_contain_text("Команда дизайна")
                expect(page.get_by_role("textbox", name="Сообщение Avelin")).to_have_attribute("placeholder", "Сообщение группе · @Avelin для обращения к агенту")
                page.get_by_role("button", name="Настройки группы", exact=True).click()
                settings_dialog = page.get_by_role("dialog", name="Команда дизайна", exact=True)
                expect(settings_dialog).to_be_visible()
                settings_dialog.get_by_role("button", name="Создать ссылку", exact=True).click()
                expect(settings_dialog.get_by_role("textbox", name="Ссылка приглашения", exact=True)).to_have_value(URL + "/?invite=test-invite-token")
                page.set_viewport_size({"width":390,"height":844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(OUT / "workspace-mobile-group-settings.png"))
                settings_dialog.get_by_role("button", name="Закрыть окно").click()
                page.screenshot(path=str(OUT / "workspace-mobile-group-chat.png"))
                page.set_viewport_size({"width":1440,"height":900})
                navigate(page,"Диалоги")
                page.locator(".chat-list-item").first.click()
                expect(page.get_by_role("textbox", name="Сообщение Avelin")).not_to_have_attribute("placeholder", "Сообщение группе · @Avelin для обращения к агенту")
                expect(page.locator(".message-role")).to_have_count(0)
                results.append("separate shared-group create/chat/settings/invite flow; private chat stays unlabelled and has no mention hint")
                private_project = next(project for project in data["projects"] if project["id"] == "project-1")
                data["projects"].append(dict(private_project,id="project-other",title="Учёба"))
                seed_thread = data["chat_threads"][0]
                data["chat_threads"].extend([dict(seed_thread,id="alpha-project-chat",project_id="project-1",title="План релиза Core",message_count=4),dict(seed_thread,id="other-project-chat",project_id="project-other",title="Только учебный проект",message_count=2)])
                memberships = {thread["id"]:thread.get("project_id") for thread in data["chat_threads"]}
                page.reload()
                navigate(page, "Главная")
                expect(page.locator(".recent-card").first).to_be_visible()
                navigate(page,"Проекты")
                expect(page.locator(".project-card")).to_have_count(2)
                assert "Семья" not in page.locator(".project-grid").inner_text()
                page.locator(".project-card").filter(has_text="Core Agent").click()
                expect(page.locator(".project-detail-header h2")).to_have_text("Core Agent")
                expect(page.locator(".project-thread")).to_have_count(1)
                expect(page.locator(".project-thread")).to_contain_text("План релиза Core")
                assert "Только учебный проект" not in page.locator(".project-workspace").inner_text()
                page.reload()
                expect(page.locator(".project-detail-header h2")).to_have_text("Core Agent")
                page.screenshot(path=str(OUT / "workspace-project-desktop.png"))
                page.locator(".project-thread").click()
                expect(page.locator(".chat-list-item")).to_have_count(1)
                expect(page.locator(".chat-header")).to_contain_text("План релиза Core")
                page.reload()
                expect(page.locator(".chat-header")).to_contain_text("План релиза Core")
                page.get_by_role("button",name="Открыть проект Core Agent",exact=True).click()
                page.locator(".project-new-chat").click()
                expect(page.locator(".chat-header")).to_contain_text("Новый диалог")
                assert chat_creation_payloads[-1]["project_id"] == "project-1"
                assert all(next(thread for thread in data["chat_threads"] if thread["id"] == identifier).get("project_id") == project_id for identifier,project_id in memberships.items())
                page.set_viewport_size({"width":390,"height":844})
                page.get_by_role("button",name="Открыть проект Core Agent",exact=True).click()
                expect(page.locator(".project-thread")).to_have_count(2)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(OUT / "workspace-project-mobile.png"))
                page.get_by_role("navigation",name="Путь проекта").get_by_role("button",name="Проекты",exact=True).click()
                page.get_by_role("button",name="Новый проект",exact=True).last.click()
                project_dialog = page.get_by_role("dialog",name="Новый проект",exact=True)
                project_dialog.get_by_role("textbox",name="Название проекта",exact=True).fill("Тихий проект")
                project_dialog.get_by_role("textbox",name="Описание проекта",exact=True).fill("Отдельная рабочая область")
                count_before = len(chat_creations)
                project_dialog.get_by_role("button",name="Создать проект",exact=True).click()
                expect(page.locator(".project-detail-header h2")).to_have_text("Тихий проект")
                expect(page.locator(".project-thread")).to_have_count(0)
                assert len(chat_creations) == count_before, "creating a project must not create an empty chat"
                page.get_by_role("button",name="Настройки проекта",exact=True).click()
                project_settings = page.get_by_role("dialog",name="Настройки проекта",exact=True)
                project_settings.get_by_role("textbox",name="Название проекта",exact=True).fill("Личный проект")
                project_settings.get_by_role("button",name="Сохранить изменения",exact=True).click()
                expect(page.locator(".project-detail-header h2")).to_have_text("Личный проект")
                navigate(page,"Диалоги")
                expect(page.locator(".chat-browser-list")).not_to_contain_text("План релиза Core")
                expect(page.locator(".chat-browser-list")).not_to_contain_text("Только учебный проект")
                page.set_viewport_size({"width":1440,"height":900})
                results.append("projects open a separate scoped workspace/chat; new project chat retains scope and existing histories; mobile back/create/settings and personal/shared isolation")
                chat_plans.append({"id":"plan-fixture","title":"Проверяемый план работы","goal":"Подготовить итоговый отчёт с источниками.","steps":["Проверить исходные данные","Собрать результаты","Проверить документ"],"acceptance_checks":["Источники сохранены","Результат можно скачать"],"status":"draft","revision":1,"digest":"fixture-plan-digest","budget":{"max_steps":20,"max_seconds":600,"max_tokens":6000}})
                page.goto(URL + "/?view=home")
                expect(page.locator(".recent-card").first).to_be_visible()
                page.locator(".recent-card").first.click()
                expect(page.get_by_role("region",name="Планы и ход работы")).to_contain_text("Проверяемый план работы")
                page.screenshot(path=str(OUT / "workspace-plan-desktop.png"))
                page.set_viewport_size({"width":390,"height":844})
                assert page.locator(".composer").evaluate("e => e.getBoundingClientRect().bottom <= innerHeight + 1")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.locator(".long-task-plans").evaluate("e => e.scrollTop=e.scrollHeight")
                page.screenshot(path=str(OUT / "workspace-plan-mobile.png"))
                page.get_by_role("button",name="Утвердить и начать",exact=True).click()
                expect(page.locator(".long-task-card")).to_contain_text("В очереди")
                assert plan_decisions == [{"decision":"approved","revision":1,"digest":"fixture-plan-digest","timezone":page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")}]
                chat_plans.clear()
                page.goto(URL + "/?view=home")
                page.set_viewport_size({"width":1440,"height":900})
                results.append("thread-scoped plan card shows steps/checks/budget, keeps mobile composer reachable and submits exact revision/digest approval")
                personal_records = populate(data)
                page.reload()
                navigate(page, "Главная")
                expect(page.locator(".recent-card").first).to_be_visible()
                failures = []
                for theme in ["light", "dark"]:
                    navigate(page, "Настройки")
                    page.get_by_role("button", name="Светлая" if theme == "light" else "Тёмная", exact=True).click()
                    for width, height in [(320, 568), (390, 844), (768, 1024), (1024, 768), (1440, 900)]:
                        page.set_viewport_size({"width": width, "height": height})
                        for title in ["Главная", "Совместно", "Проекты", "Память", "Автоматизации", "Задачи", "Файлы", "Настройки"]:
                            navigate(page, title)
                            inspect_layout(page, f"{theme}-{width}-{title}", failures)
                        for title in ["Подключения", "Инструменты", "Разрешения", "Журнал", "Общие"]:
                            page.get_by_role("navigation", name="Разделы настроек").get_by_role("button", name=title, exact=True).click()
                            inspect_layout(page, f"{theme}-{width}-settings-{title}", failures)
                            if width == 390 and title == "Разрешения":
                                page.screenshot(path=str(OUT / f"workspace-mobile-permissions-{theme}.png"), full_page=True)
                results.append("populated screens/settings: 320/390/768/1024/1440px in both themes (130 layout checks)")
                navigate(page,"Автоматизации")
                page.get_by_role("button",name="Запустить",exact=True).click()
                expect(page.get_by_text("Завершено",exact=False).last).to_be_visible()
                page.locator(".automation-result summary").click()
                expect(page.locator(".automation-result")).to_contain_text("Автоматизация подготовила документ.")
                assert len(automation_starts) == 1
                results.append("manual automation start shows server run result without changing schedule")
                page.get_by_role("button", name="Открыть диалог запуска", exact=True).click()
                expect(page.locator(".chat-card")).to_be_visible()
                results.append("automation result opens its persisted conversation for evidence and approvals")

                long_name = "Очень_длинное_название_итогового_документа_без_пробелов_" * 4
                data["history"] = [
                    {"role": "user", "content": "Проверь длинный ответ и документ.", "status": "complete"},
                    {"role": "assistant", "status": "complete", "content":
                     "## Проверка содержимого\n\n" + "Абзац с подробностями.\n\n" * 30 +
                     "| " + " | ".join(["Заголовок таблицы"] * 8) + " |\n|" + "---|" * 8 + "\n|" + "данные|" * 8 + "\n\n" +
                     "```python\n" + "long_variable_name_" * 60 + "\n```\n\n" +
                     "https://example.test/" + "long-url-" * 50 + "\n\n[Скачать отчёт](test.docx) · [Скачать через sandbox](sandbox:/mnt/data/test.docx) · [Неподтверждённый файл](other-user/secret.docx)",
                     "artifacts": [{"path": "test.docx", "name": long_name + ".docx", "extension": ".docx", "size_bytes": 4096}]}
                ]
                data["active_thread"]["title"] = long_name
                page.goto(URL + "/?view=home")
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
                page.get_by_role("button",name="Копировать",exact=True).click()
                expect(page.get_by_role("button",name="Скопировано",exact=True)).to_be_visible()
                assert page.evaluate("navigator.clipboard.readText()") == "long_variable_name_" * 60
                results.append("code block Copy writes only code text to clipboard")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator(".messages").evaluate("e => e.scrollTop = e.scrollHeight")
                with page.expect_download() as inline_download:
                    page.get_by_role("button",name="Скачать отчёт",exact=True).click()
                assert inline_download.value.suggested_filename.endswith(".docx")
                assert page.get_by_role("link",name="Неподтверждённый файл",exact=True).count() == 0
                with page.expect_download() as sandbox_download:
                    page.get_by_role("button",name="Скачать через sandbox",exact=True).click()
                assert sandbox_download.value.suggested_filename.endswith(".docx")
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
                assert len(cancelled_runs) == 1
                stream_mode = "error"
                page.get_by_role("textbox", name="Сообщение Avelin").fill("Проверка ошибки провайдера")
                page.get_by_role("button", name="Отправить", exact=True).click()
                expect(page.locator(".workspace-error")).to_contain_text("Провайдер временно недоступен")
                expect(page.locator(".message-failed").last).to_contain_text("Провайдер временно недоступен")
                inspect_layout(page, "mobile-provider-error", failures)
                assert page.locator(".composer").evaluate("e => e.getBoundingClientRect().bottom <= innerHeight + 1")
                page.screenshot(path=str(OUT / "workspace-mobile-error.png"), full_page=True)
                results.append("mobile waiting/cancel/error states keep controls visible and usable")
                page.get_by_role("button", name="Закрыть сообщение об ошибке").click()
                stream_mode = "truncated"
                page.get_by_role("textbox", name="Сообщение Avelin").fill("Проверка разрыва потока")
                page.get_by_role("button", name="Отправить", exact=True).click()
                expect(page.get_by_role("status",name="Состояние ответа")).to_contain_text("восстанавливаем подключение")
                expect(page.locator(".message-assistant").last).to_contain_text("Частичный текст")
                assert page.locator(".message-assistant").last.get_attribute("class").find("message-failed") < 0
                page.get_by_role("button",name="Остановить",exact=True).click()
                expect(page.get_by_role("button",name="Отправить",exact=True)).to_be_visible()
                results.append("truncated subscriber preserves partial response, reconnects and only explicit cancel stops server run")
                for decision, label, reply in [("approved","Разрешить","Разрешённое действие завершено."),("denied","Отклонить","Действие отклонено.")]:
                    stream_mode = "approval"
                    page.get_by_role("textbox", name="Сообщение Avelin").fill("Запрос с подтверждением")
                    page.get_by_role("button", name="Отправить", exact=True).click()
                    popup = page.get_by_role("dialog", name="Разрешить действие?",exact=True)
                    expect(popup).to_be_visible()
                    expect(popup).to_contain_text("Отправить запрос на example.test")
                    expect(popup).to_contain_text("Действует до")
                    popup.get_by_text("Подробности действия",exact=True).click()
                    expect(popup).to_contain_text("http_request")
                    expect(popup).to_contain_text("https://example.test")
                    expect(popup).to_contain_text("[REDACTED]")
                    if decision == "approved":
                        page.reload()
                        expect(popup).to_be_visible()
                        expect(popup).to_contain_text("Отправить запрос на example.test")
                    page.screenshot(path=str(OUT / f"workspace-mobile-approval-{decision}.png"))
                    original_requests = len(stream_requests)
                    popup.get_by_role("button", name=label,exact=True).click()
                    expect(popup).not_to_be_visible()
                    expect(page.locator(".message-assistant").last).to_contain_text(reply)
                    assert approval_decisions[-1] == decision
                    assert len(stream_requests) == original_requests, "approval must resume through decision endpoint, not replay prompt"
                results.append("chat approval popup survives reload and approves/denies through continuation endpoint without replaying user request")
                stream_mode = "complete"
                expect(page.get_by_role("button", name="Прикрепить файлы", exact=True)).to_be_enabled()
                page.locator('input[aria-label="Выбрать файлы"]').set_input_files({"name":"photo.png","mimeType":"image/png","buffer":png_bytes})
                expect(page.locator(".attachment-chip")).to_contain_text("photo.png")
                stream_mode = "error"
                page.get_by_role("textbox", name="Сообщение Avelin").fill("Повтори запрос с фото")
                page.get_by_role("button",name="Отправить",exact=True).click()
                expect(page.locator(".workspace-error")).to_contain_text("Провайдер временно недоступен")
                expect(page.locator(".attachment-chip")).to_contain_text("photo.png")
                expect(page.get_by_role("textbox",name="Сообщение Avelin")).to_have_value("Повтори запрос с фото")
                page.get_by_role("button",name="Закрыть сообщение об ошибке").click()
                stream_mode = "complete"
                page.evaluate("""() => {
                    const originalFetch=window.fetch.bind(window);
                    window.fetch=async (...args) => {
                        const response=await originalFetch(...args);
                        if(!String(args[0]).includes('/events?after=')) return response;
                        window.fetch=originalFetch;
                        const reader=response.body.getReader();
                        return new Response(new ReadableStream({start(controller) {
                            window.finishAcceptedStream=async () => {
                                while(true) { const part=await reader.read(); if(part.done) break; controller.enqueue(part.value); }
                                controller.close();
                            };
                        }}),{status:200,headers:response.headers});
                    };
                }""")
                page.get_by_role("textbox", name="Сообщение Avelin").fill("Посмотри на фото")
                page.get_by_role("button",name="Отправить",exact=True).click()
                expect(page.locator(".attachment-chip")).to_have_count(0)
                expect(page.get_by_role("button",name="Остановить",exact=True)).to_be_visible()
                page.wait_for_function("typeof window.finishAcceptedStream === 'function'")
                page.evaluate("window.finishAcceptedStream()")
                expect(page.get_by_role("button",name="Отправить",exact=True)).to_be_visible()
                expect(page.locator(".sent-image img")).to_be_visible()
                page.get_by_role("button",name="Открыть изображение photo.png",exact=True).click()
                image_dialog=page.get_by_role("dialog",name="photo.png",exact=True)
                expect(image_dialog.locator(".image-lightbox img")).to_be_visible()
                page.screenshot(path=str(OUT / "workspace-mobile-image-preview.png"))
                with page.expect_download() as original_download:
                    image_dialog.get_by_role("button",name="Скачать оригинал",exact=True).click()
                assert original_download.value.suggested_filename == "photo.png"
                image_dialog.get_by_role("button",name="Закрыть окно").click()
                page.reload()
                expect(page.locator(".sent-image img")).to_be_visible()
                assert preview_authorizations and all(value == "Bearer fixture-token" for value in preview_authorizations)
                results.append("sent image preview/lightbox/original download use authorization and survive history refresh")
                touch_context = browser.new_context(viewport={"width":390,"height":844},has_touch=True,is_mobile=True,reduced_motion="reduce")
                touch_context.add_init_script("localStorage.setItem('avelin-auth-token','fixture-token');")
                touch_context.route("**/api/**",route_api)
                touch_page=touch_context.new_page()
                touch_page.goto(URL + "/?view=home")
                home_input=touch_page.get_by_role("textbox",name="Новая задача")
                home_input.fill("Первая строка")
                home_input.press("Enter")
                expect(home_input).to_have_value("Первая строка\n")
                touch_page.locator(".recent-card").first.click()
                chat_input=touch_page.get_by_role("textbox",name="Сообщение Avelin")
                count_before=len(stream_requests)
                chat_input.fill("Сообщение с телефона")
                chat_input.press("Enter")
                expect(chat_input).to_have_value("Сообщение с телефона\n")
                assert len(stream_requests) == count_before
                touch_context.close()
                desktop_input=page.get_by_role("textbox",name="Сообщение Avelin")
                desktop_input.fill("Отправка Enter с компьютера")
                desktop_input.press("Enter")
                expect(page.locator(".message-assistant").last).to_contain_text("Тестовый ответ готов.")
                assert len(stream_requests) == count_before+1
                results.append("touch Enter inserts newline in home/chat; desktop Enter still sends")

                push_enabled = True
                push_context = browser.new_context(viewport={"width":390,"height":844},reduced_motion="reduce")
                push_context.add_init_script("""localStorage.setItem('avelin-auth-token','fixture-token');
                    window.pushProbe={permissionRequests:0,unsubscribed:0,subscribed:false};
                    const subscription={endpoint:'https://push.example.test/fixture',toJSON:()=>({endpoint:'https://push.example.test/fixture',keys:{p256dh:'test-public',auth:'test-auth'}}),unsubscribe:async()=>{pushProbe.subscribed=false;pushProbe.unsubscribed++;return true;}};
                    const registration={pushManager:{getSubscription:async()=>pushProbe.subscribed?subscription:null,subscribe:async()=>{pushProbe.subscribed=true;return subscription;}}};
                    Object.defineProperty(navigator,'serviceWorker',{value:{register:async()=>registration,getRegistration:async()=>registration,ready:Promise.resolve(registration)}});
                    window.PushManager=class {};
                    window.Notification=class {static permission='default';static async requestPermission(){pushProbe.permissionRequests++;this.permission='granted';return 'granted';}};
                """)
                push_context.route("**/api/**",route_api)
                push_page=push_context.new_page()
                push_page.on("pageerror",lambda error: errors.append(str(error)))
                push_page.goto(URL + "/?view=home")
                expect(push_page.get_by_role("heading",name="На чём продолжим?")).to_be_visible()
                navigate(push_page,"Настройки")
                expect(push_page.get_by_role("button",name="Включить уведомления",exact=True)).to_be_enabled()
                assert push_page.evaluate("pushProbe.permissionRequests") == 0
                push_page.get_by_role("button",name="Включить уведомления",exact=True).click()
                expect(push_page.get_by_role("button",name="Отключить уведомления",exact=True)).to_be_visible()
                assert push_requests[-1]["method"] == "PUT" and push_requests[-1]["body"]["keys"]["p256dh"] == "test-public"
                push_page.get_by_role("button",name="Проверить уведомление",exact=True).click()
                expect(push_page.get_by_role("status")).to_contain_text("поставлено в очередь")
                push_page.get_by_role("button",name="Отключить уведомления",exact=True).click()
                expect(push_page.get_by_role("button",name="Включить уведомления",exact=True)).to_be_visible()
                assert push_page.evaluate("pushProbe.unsubscribed") == 1
                push_page.get_by_role("button",name="Включить уведомления",exact=True).click()
                expect(push_page.get_by_role("button",name="Отключить уведомления",exact=True)).to_be_visible()
                push_page.get_by_role("button",name="Выйти",exact=True).click()
                expect(push_page.get_by_role("button",name="Вход",exact=True)).to_be_visible()
                assert push_page.evaluate("pushProbe.unsubscribed") == 2
                assert [request["method"] for request in push_requests if request["method"] != "GET"] == ["PUT","POST","DELETE","PUT","DELETE"]
                assert all(request["authorization"] == "Bearer fixture-token" for request in push_requests)
                push_context.close()
                results.append("push opt-in requests permission only on click; authenticated subscribe/test/unsubscribe/logout contracts (mock, not physical delivery)")
                page.goto(URL + "/?view=chat&thread=chat-2")
                expect(page.get_by_role("textbox",name="Сообщение Avelin")).to_be_visible()
                expect(page.locator('.chat-list-item[aria-current="true"]')).to_have_attribute("title",data["chat_threads"][[item["id"] for item in data["chat_threads"]].index("chat-2")]["title"])
                assert page.evaluate("localStorage.getItem('avelin-active-thread')") == "chat-2"
                assert "thread=" not in page.url
                page.goto(URL + "/?view=chat&thread=unavailable-notification-chat")
                expect(page.locator(".workspace-error")).to_contain_text("Диалог из уведомления недоступен")
                expect(page.get_by_role("textbox",name="Сообщение Avelin")).to_be_visible()
                assert page.evaluate("localStorage.getItem('avelin-active-thread')") != "unavailable-notification-chat"
                results.append("notification deep-link selects authenticated target chat once; unavailable chat safely falls back without exposing another account")
                legacy_runtime = True
                page.goto(URL + "/?view=home")
                expect(page.get_by_role("status",name="Обновление сервера")).to_contain_text("Перезапусти Avelin через лаунчер")
                legacy_runtime = False
                page.reload()
                expect(page.get_by_role("heading",name="На чём продолжим?")).to_be_visible()
                expect(page.get_by_role("status",name="Обновление сервера")).to_have_count(0)
                results.append("legacy health contract shows update guidance once; current runtime has no stale-version banner")

                auth_context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                auth_page = auth_context.new_page()
                auth_page.on("pageerror", lambda error: errors.append(str(error)))
                auth_page.goto(URL + "/?view=home")
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
            (OUT / "results.json").write_text(json.dumps({"status": "failed", "error": str(error), "completed_checks": results, "api_requests":api_requests[-45:],"runs":[public_run(item) for item in chat_runs.values()]}, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
    (OUT / "results.json").write_text(json.dumps({"status": "passed", "checks": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print("PASS:", result)


if __name__ == "__main__":
    run()
