"""Isolated browser acceptance for durable chat; no real accounts/models/servers.

Backend durability has separate real HTTP tests. This controls network cuts and
server snapshots precisely so client replay, identity and cancellation are tested.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parent))
from launch_local import ROOT, FRONTEND, NO_WINDOW, fetch, load_paths
from verify_workspace_ui import fixture

URL = 'http://127.0.0.1:4183'
OUT = ROOT / '.local' / 'chat-recovery-ui'


class Server:
    def __init__(self):
        self.accounts = {}
        for token, user_id in [('fixture-token','test-user'),('other-token','other-user')]:
            data = deepcopy(fixture()); data['user']['id'] = user_id
            data['chat_threads'] = [dict(data['chat_threads'][0], id=f'{user_id}-{suffix}', user_id=user_id, title=title, message_count=2) for suffix,title in [('a','Диалог A'),('b','Диалог B')]]
            data['active_thread'] = data['chat_threads'][0]
            self.accounts[token] = data
        self.histories = {(token,item['id']):[{'id':f"{item['id']}-old-user",'role':'user','content':'Исходный запрос '+item['title'],'status':'complete'}, {'id':f"{item['id']}-old-answer",'role':'assistant','content':'История '+item['title'],'status':'complete'}] for token,data in self.accounts.items() for item in data['chat_threads']}
        self.runs, self.by_request, self.latest = {}, {}, {}
        self.posts, self.cancels, self.subscriptions, self.unexpected = [], [], [], []
        self.mode = 'normal'
        self.held = None

    def create(self, token, body):
        request_id = body['request_id']; request_key = (token,request_id)
        if request_key in self.by_request: return self.runs[self.by_request[request_key]]
        run_id = f'run-{len(self.runs)+1}'; thread_id = body['thread_id']
        run = dict(run_id=run_id,request_id=request_id,thread_id=thread_id,state='running',text='',latest_seq=0,
                   message_id=run_id+'-assistant',user_message_id=run_id+'-user',user_text=body.get('message',''),attachments=body.get('attachments',[]),artifacts=[],approvals=[],cancel_requested=False)
        run['_token'], run['_events'] = token, []
        self.runs[run_id] = run; self.by_request[request_key] = run_id; self.latest[token,thread_id] = run_id
        self.histories[token,thread_id].extend([{'id':run['user_message_id'],'role':'user','content':run['user_text'],'status':'complete'}, {'id':run['message_id'],'role':'assistant','content':'','status':'pending'}])
        return run

    @staticmethod
    def public(run): return None if run is None else {key:deepcopy(value) for key,value in run.items() if not key.startswith('_')}

    def chunk(self, run, text, persist=True):
        run['text'] += text; run['latest_seq'] += 1
        run['_events'].append(dict(type='delta',text=text,seq=run['latest_seq'],thread_id=run['thread_id'],run_id=run['run_id']))
        if persist:
            row = next(item for item in self.histories[run['_token'],run['thread_id']] if item['id'] == run['message_id'])
            row['content'] = run['text']

    def finish(self, run, state='completed'):
        run['state'] = state; run['outcome'] = state; run['latest_seq'] += 1
        run['_events'].append(dict(type='done',state=state,outcome=state,seq=run['latest_seq'],thread_id=run['thread_id'],run_id=run['run_id']))
        row = next(item for item in self.histories[run['_token'],run['thread_id']] if item['id'] == run['message_id'])
        row.update(content=run['text'],status='cancelled' if state == 'cancelled' else 'complete')

    def route(self, route):
        request = route.request; parsed = urlparse(request.url); path = parsed.path
        token = request.headers.get('authorization','').removeprefix('Bearer ')
        if path == '/api/health': route.fulfill(json={'status':'ok','runtime':{'contract':2},'restart_required':False}); return
        if path == '/api/auth/login': route.fulfill(json={'token':'other-token','user':self.accounts['other-token']['user']}); return
        if token not in self.accounts: route.fulfill(status=401,json={'detail':'Сессия недействительна'}); return
        data = self.accounts[token]
        if path == '/api/auth/me': body = data['user']
        elif path == '/api/auth/logout': body = {'ok':True}
        elif path == '/api/bootstrap':
            thread_id = parse_qs(parsed.query).get('thread_id',[data['active_thread']['id']])[0]
            thread = next((item for item in data['chat_threads'] if item['id']==thread_id),None)
            if not thread: route.fulfill(status=404,json={'detail':'Диалог недоступен'}); return
            data['active_thread'] = thread
            body = {**data,'history':deepcopy(self.histories[token,thread_id])}
        elif path == '/api/models': body = [{'provider':'mock','label':'Test','models':['preview'],'description':'Fixture'}]
        elif path == '/api/home/suggestions': body = {'basic':[],'advanced':[],'personal':[],'personalization_enabled':False}
        elif path.endswith('/model-settings'): body = {'provider':data['provider'],'model_name':data['model'],'ollama_url':'','override':False}
        elif path == '/api/profile/avatar': route.fulfill(status=404,body=''); return
        elif path == '/api/memory': body = {'counts':{},'notes':[]}
        elif path == '/api/memory/settings': body = {'autonomous_enabled':False,'health_autonomous_enabled':False,'updated_at':''}
        elif path == '/api/memory/summary': body = {'generated_at':'','sections':{'profile':[],'preference':[],'fact':[],'episode':[]},'record_count':0,'settings':{'autonomous_enabled':False,'health_autonomous_enabled':False}}
        elif path == '/api/memory/records' or path.endswith(('/compactions','/plans')): body = []
        elif path == '/api/notifications/push/config': body = {'enabled':False,'reason':'Fixture'}
        elif path == '/api/chat/runs' and request.method == 'POST':
            incoming = request.post_data_json; self.posts.append(deepcopy(incoming))
            mode,self.mode = self.mode,'normal'
            if mode == 'never_arrived': route.abort('failed'); return
            run = self.create(token,incoming)
            if mode == 'hold_post': self.held = (route,run); return
            if mode == 'response_lost': route.abort('failed'); return
            body = self.public(run)
        elif path.startswith('/api/chat/runs/by-request/'):
            run_id = self.by_request.get((token,path.rsplit('/',1)[1])); body = self.public(self.runs.get(run_id))
        elif path.endswith('/runs/active'):
            run_id = self.latest.get((token,path.split('/')[3])); body = self.public(self.runs.get(run_id))
        elif path.startswith('/api/chat/runs/'):
            run_id = path.split('/')[4]; run = self.runs.get(run_id)
            if not run or run['_token'] != token: route.fulfill(status=404,json={'detail':'Ответ недоступен'}); return
            if path.endswith('/cancel'):
                self.cancels.append(run_id); run['cancel_requested'] = True; body = self.public(run)
            elif path.endswith('/events'):
                after = int(parse_qs(parsed.query).get('after',['0'])[0]); self.subscriptions.append((run_id,after))
                if run.get('_next_delta'):
                    self.chunk(run,run.pop('_next_delta'),persist=False)
                events = [event for event in run['_events'] if event['seq'] > after]
                route.fulfill(content_type='application/x-ndjson',body=''.join(json.dumps(event,ensure_ascii=False)+'\n' for event in events)); return
            else: body = self.public(run)
        else:
            self.unexpected.append(path); route.fulfill(status=404,json={'detail':'Unexpected test API'}); return
        route.fulfill(content_type='application/json',body=json.dumps(body,ensure_ascii=False))


def main():
    OUT.mkdir(parents=True,exist_ok=True); checks=[]; errors=[]; model=Server()
    _, node = load_paths()
    with (OUT/'preview.log').open('w',encoding='utf8') as log:
        process = subprocess.Popen([str(node),str(FRONTEND/'node_modules/vite/bin/vite.js'),'preview','--host','127.0.0.1','--port','4183','--strictPort'],cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
        try:
            for _ in range(60):
                if process.poll() is not None: raise RuntimeError('Isolated preview exited')
                if fetch(URL,timeout=.3): break
                time.sleep(.1)
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel='msedge')
                context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
                context.add_init_script("localStorage.setItem('avelin-auth-token',localStorage.getItem('avelin-auth-token')||'fixture-token');")
                context.route('**/api/**',model.route)
                page=context.new_page();page.set_default_timeout(8000); page.on('pageerror',lambda error:errors.append(str(error)))
                def visibility(hidden):
                    page.evaluate("hidden => {window.__qaHidden=hidden;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.__qaHidden});Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>window.__qaHidden?'hidden':'visible'});document.dispatchEvent(new Event('visibilitychange'));}",hidden)
                def open_a():
                    page.goto(URL+'/?view=chat&thread=test-user-a');expect(page.locator('.chat-header')).to_contain_text('Диалог A')
                def send(text,mode='normal'):
                    model.mode=mode; page.get_by_role('textbox',name='Сообщение Avelin').fill(text)
                    expect(page.get_by_role('button',name='Отправить',exact=True)).to_be_enabled()
                    page.get_by_role('button',name='Отправить',exact=True).click()
                open_a();send('Долгая задача')
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_be_visible()
                run=next(reversed(model.runs.values()));model.chunk(run,'PREFIX ')
                visibility(True);model.chunk(run,'BACKGROUND ');visibility(False)
                expect(page.locator('.message-assistant').last).to_contain_text('PREFIX BACKGROUND ')
                assert len(model.posts)==1 and not model.cancels
                # Reload sees the persisted pending assistant ID. A subsequent
                # delta is newer than that row and must replace it in place.
                run['_next_delta']='LIVE_SUFFIX'
                page.reload();expect(page.locator('.message-assistant').last).to_contain_text('PREFIX BACKGROUND LIVE_SUFFIX')
                assert page.locator('.message-user').filter(has_text='Долгая задача').count()==1
                assert page.locator('.message-assistant').filter(has_text='PREFIX BACKGROUND LIVE_SUFFIX').count()==1
                assert any(identifier==run['run_id'] and after>=2 for identifier,after in model.subscriptions)
                model.finish(run);visibility(True);visibility(False)
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_have_count(0)
                assert len(model.posts)==1 and not model.cancels
                checks.append('background/reload restores canonical pending ID and later cursor deltas; one user/assistant; no POST replay or cancel')

                send('Отмена требует подтверждения');cancelled=next(reversed(model.runs.values()));model.chunk(cancelled,'Частичный результат')
                visibility(True);visibility(False);expect(page.locator('.message-assistant').last).to_contain_text('Частичный результат')
                with page.expect_response(lambda response: response.url.endswith('/cancel')):
                    page.get_by_role('button',name='Остановить',exact=True).click()
                expect(page.get_by_role('button',name='Ждём подтверждение…',exact=True)).to_be_visible()
                assert model.cancels==[cancelled['run_id']] and cancelled['state']=='running'
                page.reload();expect(page.get_by_role('button',name='Ждём подтверждение…',exact=True)).to_be_visible()
                assert len(model.cancels)==1
                model.finish(cancelled,'cancelled');visibility(True);visibility(False)
                expect(page.locator('.message-cancelled')).to_contain_text('Частичный результат')
                expect(page.get_by_role('button',name='Ждём подтверждение…',exact=True)).to_have_count(0)
                checks.append('explicit cancel API persists intent across reload and waits for terminal server acknowledgement')

                send('POST accepted but response lost','response_lost')
                lost=next(reversed(model.runs.values()));model.chunk(lost,'FOUND_BY_REQUEST')
                visibility(True);visibility(False);expect(page.locator('.message-assistant').last).to_contain_text('FOUND_BY_REQUEST')
                assert sum(item['message']=='POST accepted but response lost' for item in model.posts)==1
                model.finish(lost);visibility(True);visibility(False)
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_have_count(0)
                checks.append('lost initial POST response is recovered by request ID without resubmitting')

                send('Не дошедшая отправка','never_arrived')
                expect(page.get_by_role('button',name='Повторить отправку',exact=True)).to_be_visible()
                original=model.posts[-1]['request_id'];page.reload()
                with page.expect_response(lambda response: urlparse(response.url).path=='/api/chat/runs' and response.request.method=='POST'):
                    page.get_by_role('button',name='Повторить отправку',exact=True).click()
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_be_visible()
                assert model.posts[-1]['request_id']==original and sum(item['request_id']==original for item in model.posts)==2
                retried=model.runs[model.by_request['fixture-token',original]];model.chunk(retried,'RETRIED_ONCE');model.finish(retried)
                visibility(True);visibility(False);expect(page.locator('.message-assistant').last).to_contain_text('RETRIED_ONCE')
                assert page.locator('.message-user').filter(has_text='Не дошедшая отправка').count()==1
                checks.append('definitive missing request is explicitly retryable after reload with the same UUID/payload and one server turn')

                # Another device starts a newer turn while local storage still
                # points to an older, terminal run in the same thread.
                newer=model.create('fixture-token',{'request_id':'other-device-request','thread_id':'test-user-a','message':'Другой клиент','attachments':[]});model.chunk(newer,'NEWER_DEVICE_RUN')
                page.reload();expect(page.locator('.message-assistant').last).to_contain_text('NEWER_DEVICE_RUN')
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_be_visible()
                model.finish(newer);visibility(True);visibility(False)
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_have_count(0)
                checks.append('saved terminal metadata cannot hide the newer server run from another device')

                send('Late thread A response','hold_post')
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_be_visible()
                page.locator('.chat-list-item').filter(has_text='Диалог B').click()
                expect(page.locator('.messages')).to_contain_text('История Диалог B')
                held,old=model.held;model.chunk(old,'PRIVATE_LATE_A');model.finish(old);held.fulfill(json=model.public(old))
                expect(page.locator('.messages')).not_to_contain_text('PRIVATE_LATE_A')
                assert page.evaluate("localStorage.getItem('avelin-active-thread')")=='test-user-b'
                assert old['run_id'] not in model.cancels
                checks.append('thread navigation detaches only the subscriber; late reply cannot replace the selected thread')

                open_a();send('Late failed subscriber in A','hold_post')
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_be_visible()
                page.locator('.chat-list-item').filter(has_text='Диалог B').click()
                expect(page.locator('.messages')).to_contain_text('История Диалог B')
                held,old=model.held;model.chunk(old,'BACKGROUND_AFTER_LOST_ACK');held.abort('failed')
                expect(page.get_by_role('status',name='Состояние ответа')).to_have_count(0)
                assert old['run_id'] not in model.cancels
                open_a();expect(page.locator('.message-assistant').last).to_contain_text('BACKGROUND_AFTER_LOST_ACK')
                assert sum(item['message']=='Late failed subscriber in A' for item in model.posts)==1
                model.finish(old);visibility(True);visibility(False)
                expect(page.get_by_role('button',name='Остановить',exact=True)).to_have_count(0)
                checks.append('late POST failure cannot alter another thread; returning recovers the accepted server run without replay')

                open_a();send('Late previous account','hold_post')
                page.locator('.sidebar').get_by_role('button',name='Настройки',exact=True).click()
                page.get_by_role('button',name='Выйти',exact=True).click()
                expect(page.get_by_role('button',name='Вход',exact=True)).to_be_visible()
                page.get_by_role('textbox',name='Email или логин').fill('other')
                page.get_by_label('Пароль',exact=True).fill('fixture-password')
                page.get_by_role('button',name='Войти',exact=True).click()
                expect(page.get_by_role('heading',name='На чём продолжим?')).to_be_visible()
                held,old=model.held;model.chunk(old,'PRIVATE_PREVIOUS_ACCOUNT');model.finish(old);held.fulfill(json=model.public(old))
                page.locator('.sidebar').get_by_role('button',name='Главная',exact=True).click()
                page.locator('.recent-card').first.click()
                expect(page.locator('.messages')).to_contain_text('История Диалог A')
                assert 'PRIVATE_PREVIOUS_ACCOUNT' not in page.locator('main').inner_text()
                assert page.evaluate("localStorage.getItem('avelin-active-thread')").startswith('other-user-')
                checks.append('logout/new account ignores late POST callbacks and never hydrates previous account runs')
                assert not errors,errors;assert not model.unexpected,model.unexpected
                browser.close()
            (OUT/'results.json').write_text(json.dumps({'status':'passed','checks':checks,'post_count':len(model.posts),'cancel_count':len(model.cancels)},ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps({'status':'passed','checks':len(checks)},ensure_ascii=False))
        except Exception as error:
            try:
                debug=page.evaluate("() => ({url:location.href,runKeys:Object.fromEntries(Object.entries(localStorage).filter(([name])=>name.startsWith('avelin-chat-run:'))),sessionKeys:Object.keys(sessionStorage),body:document.body.innerText.slice(-7000)})")
                page.screenshot(path=str(OUT/'failure.png'))
            except Exception:debug={}
            (OUT/'results.json').write_text(json.dumps({'status':'failed','error':str(error),'checks':checks,'posts':model.posts,'debug':debug},ensure_ascii=False,indent=2),encoding='utf8');raise
        finally: process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
