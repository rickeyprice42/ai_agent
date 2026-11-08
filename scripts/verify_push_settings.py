"""Isolated browser regression for iOS backgrounded push-test HTTP responses.

Uses a temporary Vite process and synthetic fetch/subscription fixtures. It never
calls a real API/push endpoint or requests a user's notification permission.
This is browser coverage, not physical iPhone delivery acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from launch_local import load_paths, NO_WINDOW

URL = 'http://127.0.0.1:4186'
OUT = ROOT / '.local/push-2026-11-05'

HTML = '''<!doctype html><html><body><div id="root"></div><script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>(type)=>type;
window.__vite_plugin_react_preamble_installed__=true;
const reactModule=await import('/node_modules/.vite/deps/react.js');
const React=reactModule.default||reactModule;
const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
const createRoot=dom.createRoot||dom.default.createRoot;
const {PushSettings}=await import('/src/PushSettings.tsx');
createRoot(document.getElementById('root')).render(React.createElement(PushSettings,{token:'synthetic-token'}));
</script></body></html>'''

FIXTURE = '''
window.probe={hidden:false,posts:[],gets:[],state:'pending',failPost:true};
Object.defineProperty(document,'visibilityState',{get:()=>probe.hidden?'hidden':'visible'});
window.PushManager=class {};
window.Notification=class {static permission='granted';static async requestPermission(){throw Error('must not prompt');}};
Object.defineProperty(navigator,'serviceWorker',{value:{getRegistration:async()=>({pushManager:{getSubscription:async()=>({endpoint:'https://web.push.apple.com/synthetic'})}})}});
window.fetch=async(url,options={})=>{
  if(url.endsWith('/config'))return new Response(JSON.stringify({enabled:true,public_key:'fixture-key'}));
  if(url.endsWith('/test')&&options.method==='POST'){
    probe.posts.push({body:JSON.parse(options.body),keepalive:options.keepalive,authorization:options.headers.Authorization});
    if(probe.failPost){probe.hidden=true;throw new TypeError('Load failed');}
    return new Response(JSON.stringify({state:'pending',queued:1}));
  }
  if(url.includes('/test/')){probe.gets.push(url);return new Response(JSON.stringify({state:probe.state,reason:probe.reason}));}
  throw Error('unexpected fixture fetch');
};
'''


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    _, node = load_paths()
    log = (OUT / 'browser-vite.log').open('w', encoding='utf-8')
    process = subprocess.Popen([str(node), str(ROOT/'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '4186', '--strictPort'],
        cwd=ROOT/'frontend', stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    results = []
    try:
        for _ in range(100):
            try:
                with urlopen(URL+'/@vite/client', timeout=1): break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('Owned Vite fixture failed to start')
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context()
            context.add_init_script(FIXTURE)
            context.route('**/__push_fixture__', lambda route: route.fulfill(body=HTML, content_type='text/html'))
            page = context.new_page()
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(URL+'/__push_fixture__')
            page.wait_for_timeout(700)
            if errors: raise RuntimeError(json.dumps(errors))
            test = page.get_by_role('button',name='Проверить уведомление')
            expect(test).to_be_enabled()
            test.click()
            expect(page.get_by_role('status')).to_contain_text('После возвращения')
            assert 'Load failed' not in page.locator('body').inner_text()
            record=page.evaluate('probe.posts[0]')
            assert record['keepalive'] and record['authorization']=='Bearer synthetic-token'
            request_id=record['body']['request_id']
            assert page.evaluate("sessionStorage.getItem('avelin-pending-push-test')")==request_id
            before=page.evaluate('probe.gets.length')
            page.wait_for_timeout(2200)
            assert page.evaluate('probe.gets.length')==before
            page.evaluate("probe.state='accepted';probe.hidden=false;document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.get_by_role('status')).to_contain_text('Служба push приняла')
            assert len(page.evaluate('probe.posts'))==1
            assert page.evaluate("sessionStorage.getItem('avelin-pending-push-test')") is None
            results.append('Lost POST response on background: keepalive request, no raw Load failed, foreground GET reconciles acceptance without duplicate POST')

            # Persisted request survives a reload; only GET is allowed on restore.
            page.evaluate("sessionStorage.setItem('avelin-pending-push-test','restored-request-12345')")
            page.reload()
            page.evaluate("probe.state='failed';probe.reason='push_http_403:BadJwtToken';document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.get_by_role('status')).to_contain_text('отклонила авторизацию сервера')
            assert page.evaluate('probe.posts.length')==0
            assert any('restored-request-12345' in path for path in page.evaluate('probe.gets'))
            results.append('Reload recovers same persisted test; Apple403 displays server authentication failure without re-subscribing or claiming delivery')
            assert not errors, errors
            browser.close()
        result={'status':'PASS','cases':results,'physical_iphone_delivery':'NOT TESTED'}
        (OUT/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()


if __name__=='__main__':run()
