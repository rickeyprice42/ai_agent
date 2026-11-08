"""Isolated production UI checks for navigation, first paint and chat actions."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parent))
from launch_local import ROOT, FRONTEND, NO_WINDOW, load_paths, fetch
from verify_chat_recovery_ui import Server

URL = 'http://127.0.0.1:4184'
OUT = ROOT / '.local' / 'workspace-state-ui'


class StateServer(Server):
    def __init__(self):
        super().__init__(); self.deleted=[]; self.chat_models={}; self.model_changes=[]; self.answers=[]; self.media_requests=[]

    def route(self, route):
        request=route.request; path=urlparse(request.url).path
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        data=self.accounts.get(token)
        if data and '/media/' in path:
            self.media_requests.append({'path':request.url,'authorization':request.headers.get('authorization')})
            if path.endswith('/preview'):route.fulfill(content_type='image/png',body=(FRONTEND/'public/brand/icon-192.png').read_bytes())
            else:route.fulfill(content_type='application/octet-stream',body=b'SHARED_AUTHOR_BYTES')
            return
        if path.endswith(('/members','/invitations','/shared-items')):route.fulfill(json=[]);return
        if data and '/questions/' in path and path.endswith('/runs') and request.method=='POST':
            answer=request.post_data_json;self.answers.append(answer);data['chat_questions']=[]
            run=self.create(token,{'request_id':answer['request_id'],'thread_id':path.split('/')[3],'message':answer.get('text') or answer.get('option_id'),'attachments':[]})
            self.chunk(run,'ANSWER_ACCEPTED');self.finish(run);route.fulfill(json=self.public(run));return
        if data and path.endswith('/model-settings'):
            if path == '/api/model-settings':
                change=request.post_data_json;data.update(provider=change['provider'],model=change['model_name']);route.fulfill(json=change);return
            thread=path.split('/')[3]; key=(token,thread)
            if request.method=='PUT': self.chat_models[key]={**request.post_data_json,'override':True};self.model_changes.append((thread,request.post_data_json))
            if request.method=='DELETE': self.chat_models.pop(key,None)
            route.fulfill(json=self.chat_models.get(key,{'provider':data['provider'],'model_name':data['model'],'ollama_url':'','override':False}));return
        if data and path.startswith('/api/chats/') and request.method=='DELETE':
            thread=path.rsplit('/',1)[1];self.deleted.append(thread)
            data['chat_threads']=[item for item in data['chat_threads'] if item['id']!=thread]
            route.fulfill(json={'ok':True});return
        if path=='/api/models':route.fulfill(json=[{'provider':'mock','label':'Test','models':['preview','alternative'],'description':'Fixture'}]);return
        if path.endswith('/media'): route.fulfill(json={'thread_id':path.split('/')[3],'images':[],'files':[],'links':[],'next_before':None});return
        super().route(route)


def main():
    OUT.mkdir(parents=True,exist_ok=True); checks=[]; server=StateServer(); errors=[]
    old=server.create('fixture-token',{'request_id':'old-terminal','thread_id':'test-user-a','message':'Предыдущий запрос'})
    server.chunk(old,'Старый ответ');server.finish(old)
    pending={'id':'new-pending','thread_id':'test-user-a','tool_name':'http_request','risk':'external','scope':{'host':'example.test'},'arguments':{'url':'https://example.test'},'summary':'Запросить страницу example.test','reason':'Чтобы проверить актуальные сведения для ответа.','status':'pending','expires_at':'2099-12-31T12:00:00Z'}
    server.accounts['fixture-token']['chat_approvals']=[pending]
    _,node=load_paths()
    with (OUT/'preview.log').open('w',encoding='utf8') as log:
        process=subprocess.Popen([str(node),str(FRONTEND/'node_modules/vite/bin/vite.js'),'preview','--host','127.0.0.1','--port','4184','--strictPort'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
        try:
            for _ in range(60):
                if process.poll() is not None:raise RuntimeError('Preview exited')
                if fetch(URL,timeout=.3):break
                time.sleep(.1)
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel='msedge')
                early=browser.new_context();early.add_init_script("localStorage.setItem('avelin-theme-mode','dark')")
                early.route('**/assets/*.js',lambda route:route.abort())
                dark=early.new_page();dark.goto(URL)
                assert dark.evaluate("document.documentElement.dataset.theme")=='dark'
                assert dark.evaluate("getComputedStyle(document.documentElement).backgroundColor")=='rgb(9, 9, 11)'
                assert dark.locator('#root').inner_html()==''
                checks.append('dark theme and background are applied before the application JavaScript loads')
                early.close()
                context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
                context.add_init_script("localStorage.setItem('avelin-auth-token',localStorage.getItem('avelin-auth-token')||'fixture-token')")
                context.route('**/api/**',server.route);page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(URL+'/?view=chat&thread=test-user-a')
                popup=page.get_by_role('dialog',name='Разрешить действие?',exact=True)
                expect(popup).to_be_visible();page.wait_for_timeout(300);expect(popup).to_be_visible()
                popup.get_by_role('button',name='Закрыть окно',exact=True).click()
                expect(page.locator('.permission-chat-card')).to_contain_text(pending['reason'])
                assert not server.posts
                checks.append('new pending approval survives stale terminal run recovery and its reason remains inline after popup dismissal')
                old['approval_history']=[{**pending,'status':'expired'}]
                page.reload();expect(popup).to_be_visible()
                expect(page.get_by_label('Недействительное разрешение',exact=True)).to_have_count(0)
                server.accounts['fixture-token']['chat_approvals']=[]
                page.reload();expect(popup).not_to_be_visible()
                expired=page.get_by_label('Недействительное разрешение',exact=True)
                expect(expired).to_contain_text('Срок разрешения истёк.')
                expect(expired).to_contain_text(pending['reason'])
                expect(expired.get_by_role('button')).to_have_count(0)
                assert not server.posts
                old['approval_history']=[{**pending,'id':'new-success','status':'consumed','action_status':'completed'},old['approval_history'][0]]
                page.reload();expect(expired).to_have_count(0)
                old['approval_history']=[{**pending,'status':'consumed','action_status':'interrupted'}]
                page.reload();expect(expired).to_contain_text('Запрос разрешения прерван.')
                expect(expired.get_by_role('button')).to_have_count(0)
                old['approval_history']=[]
                checks.append('expired permission is explained inline after reload, never replaces live pending or exposes approval/replay controls')
                page.locator('.sidebar').get_by_role('button',name='Память',exact=True).click()
                page.reload();expect(page.locator('.sidebar').get_by_role('button',name='Память',exact=True)).to_have_attribute('aria-current','page')
                page.goto(URL);expect(page.locator('.sidebar').get_by_role('button',name='Память',exact=True)).to_have_attribute('aria-current','page')
                page.evaluate("localStorage.setItem('avelin-auth-token','other-token');localStorage.removeItem('avelin-active-thread')")
                page.goto(URL);expect(page.locator('.sidebar').get_by_role('button',name='Главная',exact=True)).to_have_attribute('aria-current','page')
                assert page.get_by_role('dialog',name='Разрешить действие?',exact=True).is_visible() is False
                checks.append('section restores after reload without URL and is isolated by authenticated account')
                page.evaluate("localStorage.setItem('avelin-auth-token','fixture-token')");server.accounts['fixture-token']['chat_approvals']=[]
                page.goto(URL+'/?view=chat&thread=test-user-a');expect(page.locator('.chat-header')).to_contain_text('Диалог A')
                picker=page.get_by_role('combobox',name='Модель для диалога')
                picker.select_option(label='alternative');expect(picker).to_have_value('["mock","alternative"]')
                page.locator('.chat-list-item').filter(has_text='Диалог B').click()
                expect(picker).to_have_value('["mock","preview"]')
                page.locator('.chat-list-item').filter(has_text='Диалог A').click()
                expect(picker).to_have_value('["mock","alternative"]')
                page.reload();expect(picker).to_have_value('["mock","alternative"]')
                assert server.accounts['fixture-token']['model']=='preview'
                page.get_by_role('button',name='По умолчанию',exact=True).click()
                expect(picker).to_have_value('["mock","preview"]')
                assert not server.chat_models
                checks.append('model override is isolated per chat, survives reload and resets to the unchanged account default')
                for question_id,free_text in [('choice-question',None),('text-question','Свой развёрнутый ответ')]:
                    target_thread='test-user-b' if free_text else 'test-user-a'
                    question={'id':question_id,'thread_id':target_thread,'prompt':'Какой формат результата нужен?','options':[{'id':'docx','label':'Word'},{'id':'pdf','label':'PDF'}],'allow_free_text':True,'status':'pending'}
                    server.accounts['fixture-token']['chat_questions']=[question]
                    page.goto(URL+'/?view=chat&thread='+target_thread);card=page.get_by_role('region',name='Уточнение Avelin')
                    expect(card).to_contain_text(question['prompt'])
                    if free_text:
                        card.get_by_role('textbox',name='Свой ответ').fill(free_text)
                        card.get_by_role('button',name='Ответить',exact=True).click()
                    else:card.get_by_role('button',name='Word',exact=True).click()
                    expect(card).to_have_count(0)
                    expect(page.locator('.message-assistant').last).to_contain_text('ANSWER_ACCEPTED')
                    assert server.answers[-1].get('text' if free_text else 'option_id')==(free_text or 'docx')
                assert len(server.answers)==2 and not server.posts
                checks.append('durable clarification restores on reload and accepts choices or free text without replaying the original prompt')
                page.goto(URL+'/?view=chat&thread=test-user-a');expect(page.locator('.chat-header')).to_contain_text('Диалог A')
                page.get_by_role('button',name='Действия: Диалог B',exact=True).click()
                page.once('dialog',lambda dialog:dialog.dismiss())
                page.get_by_role('menuitem',name='Удалить диалог',exact=True).click();assert not server.deleted
                page.get_by_role('button',name='Действия: Диалог B',exact=True).click()
                page.once('dialog',lambda dialog:dialog.accept())
                page.get_by_role('menuitem',name='Удалить диалог',exact=True).click()
                expect(page.locator('.chat-list-item').filter(has_text='Диалог B')).to_have_count(0)
                assert server.deleted==['test-user-b']
                expect(page.locator('.chat-header')).to_contain_text('Диалог A')
                checks.append('desktop row menu confirms soft-delete and deleting another chat preserves the selected thread')
                page.set_viewport_size({'width':390,'height':844});page.locator('.mobile-chat-toggle').click()
                row=page.locator('.chat-list-item').filter(has_text='Диалог A')
                row.dispatch_event('pointerdown',{'pointerType':'touch','clientX':40,'clientY':250})
                expect(page.get_by_role('menuitem',name='Удалить диалог',exact=True)).to_be_visible()
                row.dispatch_event('pointerup',{'pointerType':'touch'})
                page.keyboard.press('Escape');expect(page.get_by_role('menu')).to_have_count(0)
                row.dispatch_event('pointerdown',{'pointerType':'touch','clientX':40,'clientY':250})
                row.dispatch_event('pointermove',{'pointerType':'touch','clientX':42,'clientY':280})
                page.wait_for_timeout(600);row.dispatch_event('pointerup',{'pointerType':'touch'})
                expect(page.get_by_role('menu')).to_have_count(0)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                checks.append('mobile long-press opens an accessible menu; scroll movement cancels it and Escape closes it')
                data=server.accounts['fixture-token'];group={**data['projects'][0],'id':'shared-peer-group','title':'Группа с другом','shared':True,'role':'editor','agent_mode':'mention_only','chat_count':1};data['projects'].append(group)
                shared={**data['chat_threads'][0],'id':'shared-thread','project_id':group['id'],'title':'Общий чат','message_count':2};data['chat_threads'].append(shared)
                server.histories['fixture-token',shared['id']]=[
                    {'id':'peer-image-message','role':'user','author_user_id':'peer-user','name':'Друг','content':'Вложение участника','status':'complete','artifacts':[{'path':'uploads/peer.png','name':'peer.png','extension':'.png','size_bytes':32}]},
                    {'id':'peer-result-message','role':'assistant','author_user_id':'peer-user','content':'[Скачать документ](results/shared.docx)','status':'complete','artifacts':[{'path':'results/shared.docx','name':'shared.docx','extension':'.docx','size_bytes':18}]}]
                page.set_viewport_size({'width':1440,'height':900});page.goto(URL+'/?view=shared')
                page.locator('.shared-messenger-group').filter(has_text=group['title']).click()
                expect(page.locator('.sent-image img')).to_be_visible()
                page.get_by_role('button',name='Открыть изображение peer.png',exact=True).click()
                lightbox=page.get_by_role('dialog',name='peer.png',exact=True)
                with page.expect_download() as download:lightbox.get_by_role('button',name='Скачать оригинал',exact=True).click()
                assert Path(download.value.path()).read_bytes()==b'SHARED_AUTHOR_BYTES'
                lightbox.get_by_role('button',name='Закрыть окно').click()
                with page.expect_download() as download:page.get_by_role('button',name='Скачать документ',exact=True).click()
                assert Path(download.value.path()).read_bytes()==b'SHARED_AUTHOR_BYTES'
                assert len(server.media_requests)==3 and all(item['authorization']=='Bearer fixture-token' for item in server.media_requests)
                assert any('message_id=peer-image-message' in item['path'] and '/preview?' in item['path'] for item in server.media_requests)
                assert any('message_id=peer-result-message' in item['path'] and '/file?' in item['path'] for item in server.media_requests)
                checks.append('shared member image preview/lightbox and Markdown file link use message-bound authorized endpoints, not the viewer workspace')
                assert not errors,errors
                browser.close()
            (OUT/'results.json').write_text(json.dumps({'status':'passed','checks':checks},ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps({'status':'passed','checks':len(checks)}))
        except Exception as error:
            (OUT/'results.json').write_text(json.dumps({'status':'failed','checks':checks,'error':str(error)},ensure_ascii=False,indent=2),encoding='utf8');raise
        finally:process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
