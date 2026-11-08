"""Actual App integration QA for media, suggestions, activity and Shared Workspace.

Production Vite build + browser API fixtures; no real account/model/provider calls.
"""
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from playwright.sync_api import sync_playwright,expect
import json,subprocess,sys,time
from copy import deepcopy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from launch_local import FRONTEND,NO_WINDOW,fetch,load_paths
from verify_workspace_ui import fixture,inspect_layout,navigate,NOW

OUT=ROOT/'.local/workspace-panels-2026-11-06'
URL='http://127.0.0.1:4184'


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    _,node=load_paths()
    data=fixture();data['user']['timezone']='Asia/Irkutsk'
    group=dict(data['projects'][0],id='shared-panels',title='Команда Avelin',description='Общий разговор и документы проекта',shared=True,agent_mode='mention_only',chat_count=1)
    other=dict(group,id='shared-other',title='Совместный проект с длинным названием '*5,description='Описание '*30)
    data['projects'] += [group,other]
    thread=dict(data['chat_threads'][0],id='shared-thread',project_id=group['id'],title='Группа проекта')
    data['chat_threads'].append(thread)
    data['chat_questions']=[]
    logs=[dict(id='action-'+str(i),user_id='test-user',tool_name=name,status=status,arguments={'query':'fixture'},result='TECHNICAL_SENTINEL_'+('long-data '*1500),created_at=NOW) for i,(name,status) in enumerate([('research_read','completed'),('analyze_image','validation_error'),('custom_unknown_name','waiting_approval')])]
    data['action_logs']=logs
    second=deepcopy(data);second['user']={**data['user'],'id':'second-user','username':'second','display_name':'SecondUser'}
    second['projects']=[];second['history']=[];second['action_logs']=[]
    second['chat_threads']=[dict(data['chat_threads'][0],id='second-chat',user_id='second-user',title='Second account chat')]
    second['active_thread']=second['chat_threads'][0];second['active_project']=None
    png=(FRONTEND/'public/brand/icon-192.png').read_bytes()
    download_bytes=b'Authorized thread file fixture, original bytes preserved.'
    errors=[];unexpected=[];requests=[];layouts=[];checks=[];personal_enabled=True
    def media_file(tid,identifier,name,extension='.png',available=True):
        query=f'message_id=message-source&path=outputs%2F{name}'
        return dict(id=identifier,path='outputs/'+name,name=name,extension=extension,size_bytes=len(png),available=available,
            download_url=f'/api/chats/{tid}/media/file?'+query,preview_url=f'/api/chats/{tid}/media/preview?'+query if extension=='.png' else None,message_id='message-source',created_at=NOW)
    def handle(route):
        nonlocal personal_enabled
        request=route.request;parsed=urlparse(request.url);path=parsed.path;query=parse_qs(parsed.query)
        account_b=request.headers.get('authorization')=='Bearer fixture-token-b'
        scoped=second if account_b else data
        requests.append({'path':path,'method':request.method,'authorization':request.headers.get('authorization'),'query':parsed.query})
        body=None
        if path=='/api/health':body={'status':'ok','runtime':{'contract':2},'restart_required':False}
        elif path=='/api/auth/me':body=scoped['user']
        elif path=='/api/auth/logout':body={'ok':True}
        elif path=='/api/auth/login':body={'token':'fixture-token-b','user':second['user']}
        elif path=='/api/profile/avatar':route.fulfill(status=404,json={'detail':'No avatar'});return
        elif path=='/api/bootstrap':
            tid=query.get('thread_id',[scoped['active_thread']['id']])[0]
            current=next((t for t in scoped['chat_threads'] if t['id']==tid),None)
            if current is None:route.fulfill(status=404,json={'detail':'Other account chat unavailable'});return
            scoped['active_thread']=current;scoped['active_project']=group if current.get('project_id')==group['id'] else None
            artifacts=[dict(path='outputs/shared-note.txt',name='shared-note.txt',extension='.txt',size_bytes=len(download_bytes)),dict(path='outputs/image.png',name='image.png',extension='.png',size_bytes=len(png))]
            scoped['history']=[] if account_b else [dict(id='message-source',role='assistant',content='Документ подготовлен.',status='complete',name='Участник' if current.get('project_id') else None,artifacts=artifacts)]
            body=scoped
        elif path=='/api/home/suggestions':
            idea=lambda level,index:dict(id=f'{level}-{index}',title=f'{"Обычная идея" if level=="basic" else "Большая задача" if level=="advanced" else "Мой проект"} {index}',prompt=f'Помоги выполнить {level} задачу {index} с понятным результатом.')
            body={'basic':[idea('basic',i) for i in range(6)],'advanced':[idea('advanced',i) for i in range(4)],'personal':[] if account_b else [dict(idea('personal',i),source='project',source_id='private-project') for i in range(2)],'personalization_enabled':False if account_b else personal_enabled}
        elif path.endswith('/media'):
            tid=path.split('/')[3];first='before' not in query
            if account_b:body={'thread_id':tid,'images':[],'files':[],'links':[],'next_before':None};route.fulfill(status=200,json=body);return
            images=[media_file(tid,'image-1','image.png'),media_file(tid,'image-2','Длинное_название_изображения_'*4+'.png')]
            files=[media_file(tid,'file-1','report.docx','.docx'),media_file(tid,'deleted','removed.docx','.docx',False)]
            if not first:files.append(media_file(tid,'older','older-report.txt','.txt'))
            body={'thread_id':tid,'images':images,'files':files,'links':[dict(id='link-1',url='https://example.org/reference',title='Проверенный источник',message_id='message-source',created_at=NOW),dict(id='unsafe',url='javascript:alert(1)',title='Unsafe link')], 'next_before':'earlier-fixture' if first else None}
        elif '/media/preview' in path:route.fulfill(status=200,content_type='image/png',body=png);return
        elif '/media/file' in path:route.fulfill(status=200,content_type='application/octet-stream',body=download_bytes);return
        elif path=='/api/workspace/preview':route.fulfill(status=200,content_type='image/png',body=png);return
        elif path.startswith('/api/workspace/files/'):
            unexpected.append('global-file-download-used-instead-of-thread-media')
            route.fulfill(status=403,json={'detail':'Shared file must use thread media authorization'});return
        elif path=='/api/models':body=[{'provider':'mock','label':'Тестовая модель','models':['preview'],'description':'Fixture'}]
        elif path.endswith('/model-settings'):body={'provider':'mock','model_name':'preview','reasoning_fallback_enabled':False,'scope':'global','inherited':True}
        elif path.endswith(('/runs/active','/active-run')):body=None
        elif path=='/api/notifications/push/config':body={'enabled':False,'public_key':None,'reason':'Fixture has no real push.'}
        elif path=='/api/memory':body={'counts':{key:0 for key in ['notes','global_notes','project_notes','chat_notes','memory_index','chat_messages','tasks','action_logs','personal_records','health_records']},'notes':[]}
        elif path=='/api/memory/records':body=[]
        elif path=='/api/memory/settings':body={'autonomous_enabled':False,'health_autonomous_enabled':False,'updated_at':NOW}
        elif path=='/api/memory/summary':body={'status':'disabled','settings':{'autonomous_enabled':False,'health_autonomous_enabled':False},'sections':{},'record_count':0,'explanation':'Fixture'}
        elif path.endswith('/members'):body=[dict(project_id=group['id'],user_id='test-user',display_name='Rickey',email='fixture@example.test',username='fixture',role='owner',status='active',joined_at=NOW)]
        elif path.endswith(('/invitations','/shared-items','/plans','/compactions')):body=[]
        else:unexpected.append(path);route.fulfill(status=404,json={'detail':'Unmocked panel API'});return
        route.fulfill(status=200,content_type='application/json',body=json.dumps(body,ensure_ascii=False))
    with (OUT/'preview.log').open('w',encoding='utf-8') as log:
        server=subprocess.Popen([str(node),str(FRONTEND/'node_modules/vite/bin/vite.js'),'preview','--host','127.0.0.1','--port','4184','--strictPort'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
        try:
            for _ in range(70):
                if server.poll() is not None:raise RuntimeError('Preview failed; see preview.log')
                if fetch(URL,timeout=.4):break
                time.sleep(.1)
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel='msedge')
                context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True,reduced_motion='reduce',timezone_id='Asia/Irkutsk')
                context.add_init_script("localStorage.setItem('avelin-auth-token','fixture-token');localStorage.setItem('avelin-theme-mode','light');")
                context.route('**/api/**',handle)
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(URL+'?view=home')
                expect(page.locator('.home-idea')).to_have_count(3)
                original=page.locator('.home-idea').first.inner_text()
                page.get_by_role('button',name='Другие идеи').click()
                expect(page.locator('.home-idea').first).not_to_have_text(original)
                page.get_by_role('button',name='Большие задачи',exact=True).click()
                expect(page.locator('.home-idea').first).to_contain_text('Большая задача')
                page.get_by_role('button',name='Для тебя',exact=True).click()
                expect(page.locator('.home-idea').first).to_contain_text('Мой проект')
                checks.append('Suggestion categories and rotation use returned prompts; personal gated by server consent flag')
                for width,height in [(1440,1000),(390,844),(320,760)]:
                    page.set_viewport_size({'width':width,'height':height});inspect_layout(page,'home-panels-'+str(width),layouts)
                page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'home-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000});navigate(page,'Диалоги')
                page.get_by_role('button',name='Вложения диалога',exact=True).click()
                drawer=page.get_by_role('dialog',name='Вложения диалога',exact=True)
                expect(drawer).to_be_visible();expect(drawer.locator('.thread-media-thumbnail')).to_have_count(2)
                drawer.get_by_role('tab',name='Изображения',exact=True).focus();page.keyboard.press('ArrowRight')
                expect(drawer.get_by_role('tab',name='Файлы',exact=True)).to_be_focused()
                expect(drawer.get_by_role('button',name='removed.docx')).to_be_disabled()
                with page.expect_download() as transfer:drawer.get_by_role('button',name='report.docx').click()
                download=transfer.value;assert download.suggested_filename=='report.docx';assert Path(download.path()).read_bytes()==download_bytes
                drawer.get_by_role('button',name='Показать ранние вложения').click()
                expect(drawer.get_by_role('button',name='older-report.txt')).to_be_visible()
                drawer.get_by_role('tab',name='Изображения').click();expect(drawer.locator('.thread-media-thumbnail')).to_have_count(2)
                drawer.get_by_role('tab',name='Ссылки').click();expect(drawer.locator('.thread-media-links a')).to_have_count(1)
                assert drawer.locator('.thread-media-links a').get_attribute('href')=='https://example.org/reference'
                drawer.get_by_role('tab',name='Изображения').click();drawer.locator('.thread-media-thumbnail').first.click()
                expect(page.locator('.thread-media-preview > img')).to_be_visible()
                page.get_by_role('button',name='← К вложениям',exact=True).click()
                for theme in ['light','dark']:
                    page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
                    for width,height in [(1440,1000),(390,844),(320,760)]:
                        page.set_viewport_size({'width':width,'height':height});inspect_layout(page,f'media-{theme}-{width}',layouts)
                    page.screenshot(path=str(OUT/f'media-mobile-{theme}.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                page.keyboard.press('Escape');expect(drawer).not_to_be_visible()
                expect(page.get_by_role('button',name='Вложения диалога',exact=True)).to_be_focused()
                checks.append('Thread media tabs, keyboard, disabled missing files, authorized real Blob download, preview, pagination dedup, safe links and Escape')
                page.set_viewport_size({'width':1440,'height':1000});navigate(page,'Совместно')
                expect(page.locator('.shared-messenger-group')).to_have_count(2)
                page.get_by_role('textbox',name='Найти группу').fill('Avelin');expect(page.locator('.shared-messenger-group')).to_have_count(1)
                page.get_by_role('textbox',name='Найти группу').fill('')
                for width,height in [(1440,1000),(390,844),(320,760)]:page.set_viewport_size({'width':width,'height':height});inspect_layout(page,'shared-index-'+str(width),layouts)
                page.screenshot(path=str(OUT/'shared-index-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000});page.locator('.shared-messenger-group').first.click()
                expect(page.locator('.shared-messenger-header')).to_be_visible()
                expect(page.locator('.sent-image-preview img')).to_be_visible()
                for width,height in [(1440,1000),(390,844),(320,760)]:
                    page.set_viewport_size({'width':width,'height':height});inspect_layout(page,'shared-header-'+str(width),layouts)
                    if width<=600:
                        group_box=page.locator('.shared-messenger-header').bounding_box();model_box=page.locator('.chat-header > .chat-model-picker').bounding_box()
                        assert group_box and model_box and model_box['y']>=group_box['y']+group_box['height']-1,'Model picker overlaps shared group title or participant status'
                page.screenshot(path=str(OUT/'shared-chat-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                with page.expect_download():page.locator('.message-artifacts .artifact-card[title="shared-note.txt"]').click()
                assert any('/shared-thread/media/file' in item['path'] for item in requests)
                assert any('/shared-thread/media/preview' in item['path'] for item in requests)
                page.get_by_role('button',name='Вложения диалога',exact=True).click();expect(page.get_by_role('dialog',name='Вложения диалога')).to_be_visible()
                expect(page.get_by_role('dialog',name='Вложения диалога').locator('.thread-media-thumbnail img')).to_have_count(2)
                page.keyboard.press('Escape')
                checks.append('Shared messenger index/search/header and cross-author file uses thread media route')
                navigate(page,'Настройки');page.get_by_role('button',name='Журнал',exact=True).click()
                expect(page.locator('.compact-action-log .compact-action')).to_have_count(3)
                expect(page.locator('.compact-action-log')).to_contain_text('Прочитал источник')
                expect(page.locator('.compact-action-log')).to_contain_text('Нужны другие параметры')
                expect(page.locator('.action-technical pre').first).not_to_be_visible()
                page.locator('.action-technical summary').first.click();expect(page.locator('.action-technical pre').last).not_to_be_visible()
                expect(page.locator('.action-technical').first).to_contain_text('TECHNICAL_SENTINEL_')
                for width,height in [(1440,1000),(390,844),(320,760)]:page.set_viewport_size({'width':width,'height':height});inspect_layout(page,'activity-'+str(width),layouts)
                page.screenshot(path=str(OUT/'activity-mobile.png'),full_page=True)
                checks.append('Human action labels/localized statuses and technical data collapsed by default')
                navigate(page,'Главная');personal_enabled=False;page.reload();expect(page.locator('.home-idea')).to_have_count(3)
                expect(page.get_by_role('button',name='Для тебя',exact=True)).to_have_count(0)
                navigate(page,'Настройки');page.get_by_role('button',name='Общие',exact=True).click();page.get_by_role('button',name='Выйти',exact=True).click()
                expect(page.get_by_role('textbox',name='Email или логин',exact=True)).to_be_visible()
                page.get_by_role('textbox',name='Email или логин',exact=True).fill('second-fixture')
                page.get_by_label('Пароль',exact=True).fill('synthetic-only-password')
                page.get_by_role('button',name='Войти',exact=True).click()
                expect(page.locator('.sidebar')).to_be_attached()
                navigate(page,'Главная')
                expect(page.locator('.home-heading')).to_contain_text('SecondUser')
                expect(page.get_by_role('button',name='Для тебя',exact=True)).to_have_count(0)
                page.set_viewport_size({'width':1440,'height':1000})
                navigate(page,'Диалоги');page.get_by_role('button',name='Вложения диалога',exact=True).click()
                expect(page.get_by_role('dialog',name='Вложения диалога')).to_contain_text('Пока нет изображений')
                expect(page.locator('.thread-media-thumbnail')).to_have_count(0)
                expect(page.get_by_role('dialog',name='Вложения диалога')).not_to_contain_text('image.png')
                page.keyboard.press('Escape')
                assert all(item['authorization'] in {'Bearer fixture-token','Bearer fixture-token-b'} for item in requests if '/media/' in item['path'])
                assert all(item['authorization']=='Bearer fixture-token-b' for item in requests if '/second-chat/media' in item['path'])
                assert all('fixture-token' not in item['query'] for item in requests)
                assert not page.get_by_role('button',name='Открыть рабочую папку',exact=True).count()
                assert not layouts,layouts
                assert not errors,errors
                assert not unexpected,unexpected
                checks.append('No account data without personalization consent; auth stays in headers; no JS errors, unexpected routes or horizontal overflow')
                checks.append('Synthetic logout/login in the same App clears previous account personal ideas, media and active chat')
                context.close();browser.close()
            result={'status':'PASS','checks':checks,'layout_failures':layouts,'errors':errors,'unexpected':unexpected,'media_requests':sum('/media' in item['path'] for item in requests)}
        except Exception as exc:
            result={'status':'FAIL','error':str(exc),'checks':checks,'layout_failures':layouts,'errors':errors,'unexpected':unexpected,'last_requests':requests[-25:]}
            raise
        finally:
            server.terminate();server.wait(timeout=10)
            if 'result' in locals():(OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':run()
