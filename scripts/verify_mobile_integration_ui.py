"""Real isolated ASGI handlers + built UI; phone viewport/insets are simulated.

No model calls, production server, real account or push subscription is used.
"""
from io import BytesIO
import json
import logging
import os
import re
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'scripts')]
from launch_local import FRONTEND, NO_WINDOW, fetch, load_paths

OUT = ROOT / '.local' / 'mobile-integration'
URL = 'http://127.0.0.1:4182'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks, requests, errors = [], [], []
    failure = {'path': ''}
    (OUT / 'results.json').write_text('{"status":"running"}', encoding='utf-8')
    with TemporaryDirectory(prefix='avelin-mobile-qa-') as temporary:
        isolated = Path(temporary)
        os.environ.update(DATABASE_FILE=str(isolated / 'qa.sqlite'), MEMORY_FILE=str(isolated / 'memory.json'),
            TOOL_WORKSPACE_DIR=str(isolated / 'workspace'), MODEL_PROVIDER='mock', MODEL_NAME='mock-local',
            REASONING_FALLBACK_ENABLED='false', OLLAMA_URL='http://127.0.0.1:9',
            AVELIN_LOG_DIR=str(isolated / 'logs'))
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.routes import agent_service
        from PIL import Image
        from playwright.sync_api import sync_playwright, expect
        logging.disable(logging.INFO)
        client = TestClient(app)  # No lifespan workers: no delivery/scheduler/model activity.

        def register(name):
            response = client.post('/api/auth/register', json={'email': name + '@test.example',
                'username': name, 'password': 'test-password-123', 'display_name': name})
            assert response.status_code == 200, response.text
            return response.json()
        owner, other = register('mobile-owner'), register('mobile-other')
        headers = {'Authorization': 'Bearer ' + owner['token']}
        data = BytesIO()
        Image.new('RGB', (800, 400), '#8f48bd').save(data, 'HEIF')
        heic = data.getvalue()
        uploaded = client.post('/api/attachments', headers={**headers, 'X-Filename': 'iphone.HEIC'}, content=heic)
        assert uploaded.status_code == 200, uploaded.text
        descriptor = uploaded.json()
        thread = client.post('/api/chats', headers=headers, json={'title': 'Фото с iPhone'}).json()['thread']
        agent_service.database.add_message('user', 'Проверка изображения', thread_id=thread['id'],
            artifacts=[{**descriptor, '_user_attachment': True, '_display_content': 'Проверка изображения'}])
        agent_service.database.add_message('assistant', 'Фотография получена.', thread_id=thread['id'])
        project_a = client.post('/api/projects', headers=headers, json={'title':'Core workspace','description':'Личный проект A'}).json()['project']
        project_b = client.post('/api/projects', headers=headers, json={'title':'Study workspace','description':'Личный проект B'}).json()['project']
        shared = client.post('/api/projects', headers=headers, json={'title':'Shared team','shared':True}).json()['project']
        for project, title, content in [(project_a,'Alpha project chat','ALPHA_ONLY_CONTEXT'),(project_b,'Beta project chat','BETA_ONLY_CONTEXT'),(shared,'Shared chat','SHARED_ONLY_CONTEXT')]:
            scoped = client.post('/api/chats', headers=headers,json={'title':title,'project_id':project['id']}).json()['thread']
            agent_service.database.add_message('user',content,thread_id=scoped['id'])
            agent_service.database.add_message('assistant','Контекст сохранён.',thread_id=scoped['id'])
        # A preview must remain protected even if another authenticated user knows the exact path.
        assert client.get('/api/workspace/preview', params={'path': descriptor['path']},
            headers={'Authorization': 'Bearer ' + other['token']}).status_code == 404

        def api_route(route):
            request = route.request
            parsed = urlparse(request.url)
            path = parsed.path + ('?' + parsed.query if parsed.query else '')
            if parsed.path == failure['path']:
                route.fulfill(status=404, json={'detail': 'Not Found'})
                return
            response = client.request(request.method, path, content=request.post_data_buffer,
                headers={key: value for key, value in request.headers.items() if key.lower() in
                    {'authorization', 'content-type', 'x-filename', 'accept'}})
            requests.append({'method': request.method, 'path': parsed.path, 'status': response.status_code})
            route.fulfill(status=response.status_code, body=response.content,
                headers={key: value for key, value in response.headers.items() if key.lower() in
                    {'content-type', 'cache-control', 'x-content-type-options'}})

        _, node = load_paths()
        with (OUT / 'preview.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen([str(node), str(FRONTEND / 'node_modules/vite/bin/vite.js'),
                'preview', '--host', '127.0.0.1', '--port', '4182', '--strictPort'], cwd=FRONTEND,
                stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            try:
                for _ in range(60):
                    if server.poll() is not None:
                        raise RuntimeError('Preview exited; inspect preview.log')
                    if fetch(URL, timeout=.5): break
                    time.sleep(.1)
                with sync_playwright() as p:
                    webkit = Path(p.webkit.executable_path).is_file()
                    browser = p.webkit.launch(headless=True) if webkit else p.chromium.launch(headless=True, channel='msedge')
                    engine = 'webkit' if webkit else 'chromium-with-iphone-safari-user-agent'
                    context = browser.new_context(**p.devices['iPhone 13'], timezone_id='Asia/Irkutsk', reduced_motion='reduce')
                    context.add_init_script('localStorage.setItem("avelin-auth-token",' + json.dumps(owner['token']) + ');')
                    context.add_init_script("""document.addEventListener('DOMContentLoaded',() => {
                        for(const [edge,value] of Object.entries({top:54,bottom:34,left:12,right:12})) document.documentElement.style.setProperty('--safe-'+edge,value+'px');
                        const bar=document.createElement('div');bar.id='simulated-system-statusbar';bar.textContent='9:41';bar.style.cssText='position:fixed;inset:0 0 auto;height:54px;background:#000;color:#fff;z-index:99999;padding:14px 28px;box-sizing:border-box;';document.body.append(bar);
                    });""")
                    context.route('**/api/**', api_route)
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(URL)
                    expect(page.get_by_role('heading', name='На чём продолжим?')).to_be_visible()

                    def menu():
                        button = page.get_by_role('button', name='Открыть навигацию')
                        box = button.bounding_box()
                        assert box and box['y'] >= 54 and box['height'] >= 44, box
                        point = {'x': box['x'] + box['width']/2, 'y': box['y'] + box['height']/2}
                        assert button.evaluate('(element, point) => element.contains(document.elementFromPoint(point.x,point.y))', point)
                        button.tap()
                        expect(page.locator('.nav-close')).to_be_visible()
                        assert page.locator('.nav-close').bounding_box()['y'] >= 54

                    def navigate(title):
                        menu()
                        page.locator('.sidebar').get_by_role('button', name=title, exact=True).tap()

                    menu()
                    page.screenshot(path=str(OUT / 'iphone-safe-area-navigation.png'))
                    page.locator('.nav-close').tap()
                    checks.append('54px status overlay: hamburger and drawer close remain exposed and tappable')

                    navigate('Настройки')
                    page.locator('input[aria-label="Выбрать фото профиля"]').set_input_files({'name':'iphone.HEIC','mimeType':'image/heic','buffer':heic})
                    crop = page.get_by_role('dialog', name='Фото профиля', exact=True)
                    expect(crop).to_be_visible()
                    assert crop.bounding_box()['y'] >= 54
                    assert crop.bounding_box()['y'] + crop.bounding_box()['height'] <= 844 - 34
                    page.screenshot(path=str(OUT / 'iphone-real-heic-avatar-crop.png'))
                    crop.get_by_role('button', name='Сохранить фото', exact=True).tap()
                    expect(crop).not_to_be_visible()
                    expect(page.locator('.profile-avatar')).to_be_visible()
                    actual = client.get('/api/profile/avatar', headers=headers)
                    assert actual.status_code == 200 and Image.open(BytesIO(actual.content)).size == (256,256)
                    assert client.get('/api/profile/avatar', headers={'Authorization':'Bearer '+other['token']}).status_code == 404
                    checks.append('real HEIC upload/normalize/crop/save/avatar reload with tenant isolation')

                    failure['path'] = '/api/profile/avatar/preview'
                    page.locator('input[aria-label="Выбрать фото профиля"]').set_input_files({'name':'old-server.HEIC','mimeType':'image/heic','buffer':heic})
                    expect(page.get_by_role('alert')).to_contain_text('запущенной версии сервера')
                    expect(page.get_by_role('alert')).to_be_in_viewport()
                    failure['path'] = '/api/notifications/push/config'
                    page.reload()
                    expect(page.get_by_role('heading',name='Настройки',exact=True)).to_be_visible()
                    expect(page.locator('.push-settings')).to_contain_text('запущенной версии сервера')
                    assert 'Not Found' not in page.locator('.push-settings').inner_text()
                    failure['path'] = ''
                    checks.append('old backend 404 gives visible avatar/push version guidance, not raw Not Found')

                    navigate('Главная')
                    page.locator('.recent-card').first.tap()
                    expect(page.locator('.sent-image img')).to_be_visible()
                    page.get_by_role('button',name='Открыть изображение iphone.HEIC',exact=True).tap()
                    preview = page.get_by_role('dialog',name='iphone.HEIC',exact=True)
                    expect(preview.locator('img')).to_be_visible()
                    assert preview.bounding_box()['y'] >= 54
                    page.screenshot(path=str(OUT / 'iphone-real-heic-preview.png'))
                    with page.expect_download() as download:
                        preview.get_by_role('button',name='Скачать оригинал',exact=True).tap()
                    assert Path(download.value.path()).read_bytes() == heic
                    preview.get_by_role('button',name='Закрыть окно').tap()
                    checks.append('real authorized HEIC preview and byte-identical original download')

                    failure['path'] = '/api/workspace/preview'
                    page.reload()
                    expect(page.locator('.sent-image-fallback')).to_contain_text('запущенной версии сервера')
                    with page.expect_download() as download:
                        page.locator('.sent-image-fallback').get_by_role('button',name='Скачать оригинал',exact=True).tap()
                    assert Path(download.value.path()).read_bytes() == heic
                    failure['path'] = ''
                    checks.append('missing preview route retains an authenticated original download')

                    navigate('Автоматизации')
                    expect(page.locator('.timezone-value')).to_contain_text('Asia/Irkutsk')
                    expect(page.locator('.timezone-field')).to_contain_text('Определён по устройству')
                    page.get_by_role('button',name='Изменить часовой пояс',exact=True).tap()
                    zone = page.get_by_role('dialog',name='Часовой пояс',exact=True)
                    zone.get_by_label('Часовой пояс IANA').fill('Not/AZone')
                    zone.get_by_label('Часовой пояс IANA').press('Enter')
                    expect(zone.get_by_role('alert')).to_contain_text('существующий')
                    zone.get_by_label('Часовой пояс IANA').fill('Europe/Moscow')
                    zone.get_by_role('button',name='Применить',exact=True).tap()
                    expect(zone).not_to_be_visible()
                    expect(page.locator('.timezone-value')).to_contain_text('Europe/Moscow')
                    page.get_by_role('button',name='Создать автоматизацию',exact=True).tap()
                    expect(page.locator('.task-list')).to_contain_text('Еженедельный черновик')
                    stored = client.get('/api/bootstrap',headers=headers).json()['automations']
                    assert stored and stored[0]['timezone'] == 'Europe/Moscow'
                    assert not errors, errors
                    checks.append('device timezone defaults compactly; invalid IANA rejected; chosen timezone reaches real automation API')
                    with agent_service.database.connect() as connection:
                        original_messages = [tuple(row) for row in connection.execute('SELECT id,thread_id,role,content FROM messages ORDER BY id')]
                    navigate('Проекты')
                    expect(page.locator('.project-card')).to_have_count(2)
                    page.locator('.project-card').filter(has_text='Core workspace').tap()
                    expect(page.locator('.project-detail-header h2')).to_have_text('Core workspace')
                    expect(page.locator('.project-thread')).to_have_count(1)
                    expect(page.locator('.project-thread')).to_contain_text('Alpha project chat')
                    page.locator('.project-thread').tap()
                    expect(page.locator('.messages')).to_contain_text('ALPHA_ONLY_CONTEXT')
                    expect(page.locator('.messages')).not_to_contain_text('BETA_ONLY_CONTEXT')
                    page.get_by_role('button',name='Открыть проект Core workspace',exact=True).tap()
                    page.locator('.project-new-chat').tap()
                    expect(page.locator('.chat-header strong')).to_have_text(re.compile(r'^(New chat|Новый диалог)$'))
                    scoped_threads = client.get('/api/chats',headers=headers,params={'project_id':project_a['id']}).json()
                    assert len(scoped_threads) == 2 and all(item['project_id'] == project_a['id'] for item in scoped_threads)
                    page.get_by_role('button',name='Открыть проект Core workspace',exact=True).tap()
                    page.screenshot(path=str(OUT / 'iphone-real-project-workspace.png'))
                    page.get_by_role('navigation',name='Путь проекта').get_by_role('button',name='Проекты',exact=True).tap()
                    expect(page.locator('.project-card')).to_have_count(2)
                    navigate('Диалоги')
                    expect(page.locator('.chat-browser-list')).not_to_contain_text('Alpha project chat')
                    expect(page.locator('.chat-browser-list')).not_to_contain_text('Beta project chat')
                    page.locator('.chat-list-item').filter(has_text='Фото с iPhone').tap()
                    expect(page.locator('.messages')).to_contain_text('Фотография получена.')
                    expect(page.locator('.messages')).not_to_contain_text('ALPHA_ONLY_CONTEXT')
                    with agent_service.database.connect() as connection:
                        self_same = [tuple(row) for row in connection.execute('SELECT id,thread_id,role,content FROM messages ORDER BY id')]
                    assert self_same == original_messages, 'project navigation/new draft must not move or rewrite existing messages'
                    checks.append('real API project workspace scopes history, creates only its own draft, excludes shared/personal chats and preserves all original messages')
                    browser.close()
                (OUT / 'results.json').write_text(json.dumps({'status':'passed','engine':engine,
                    'checks':checks,'requests':requests,'limitations':'ASGI integration with simulated phone/status-bar; physical iPhone push delivery not tested.'},ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'status':'passed','engine':engine,'checks':len(checks)},ensure_ascii=False))
            except Exception as error:
                (OUT / 'results.json').write_text(json.dumps({'status':'failed','error':str(error),'checks':checks,'requests':requests},ensure_ascii=False,indent=2),encoding='utf-8')
                raise
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    main()
