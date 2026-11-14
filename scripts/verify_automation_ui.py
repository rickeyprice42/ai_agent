"""Mobile form acceptance with synthetic API data against the built frontend."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_workspace_ui import fixture

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    data=fixture();requests=[]
    output=ROOT/'.local'/'automation-ui'
    output.mkdir(parents=True,exist_ok=True)
    url='http://127.0.0.1:4183'
    def route_api(route):
        path=urlparse(route.request.url).path
        if path=='/api/auth/me':body=data['user']
        elif path=='/api/bootstrap':body=data
        elif path=='/api/automations' and route.request.method=='POST':
            requests.append(route.request.post_data_json)
            body={'id':'synthetic','name':'Урок VBA'}
        elif path=='/api/health':body={'status':'ok','runtime':{'contract':2},'restart_required':False}
        elif path=='/api/model-settings':body={'provider':'mock','model_name':'preview'}
        elif path.endswith('/model-settings'):body={'provider':'mock','model_name':'preview','override':False}
        else:
            route.fulfill(status=404,json={'detail':'Optional fixture endpoint is unavailable'})
            return
        route.fulfill(status=200,json=body)
    with (output/'preview.log').open('w') as log:
        server=subprocess.Popen([shutil.which('node'),str(ROOT/'frontend/node_modules/vite/bin/vite.js'),
            'preview','--host','127.0.0.1','--port','4183','--strictPort'],cwd=ROOT/'frontend',
            stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(50):
                try:
                    with urlopen(url,timeout=.5):break
                except OSError:time.sleep(.1)
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel='msedge')
                context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                context.add_init_script("localStorage.setItem('avelin-auth-token','synthetic-token')")
                context.route('**/api/**',route_api)
                page=context.new_page()
                page.on('pageerror',lambda error:print('PAGE_ERROR',str(error)))
                page.goto(url+'/?view=automations')
                expect(page.get_by_role('heading',name='Расписания Avelin')).to_be_visible()
                title=page.get_by_placeholder('Название появится из задания')
                expect(title).to_have_value('')
                expect(page.locator('.automation-form textarea')).to_have_value('')
                page.locator('.automation-form textarea').fill('Раз в неделю проводи урок VBA для Office 2007.')
                page.locator('.automation-form select').first.select_option('weekly')
                page.get_by_role('button',name='Создать автоматизацию',exact=True).click()
                page.wait_for_timeout(400)
                if page.locator('.automation-form textarea').count()==0:
                    print('AFTER_SUBMIT',page.url,page.locator('body').inner_text()[:1000])
                expect(page.locator('.automation-form textarea')).to_have_value('')
                assert requests and requests[0]['name']==''
                assert requests[0]['budget']['max_tokens']==180000
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(output/'mobile.png'),full_page=True)
                browser.close()
            print(json.dumps({'status':'passed','form':'mobile','automatic_name':True,'budget':180000}))
        finally:
            server.terminate();server.wait(timeout=10)

if __name__=='__main__':main()
