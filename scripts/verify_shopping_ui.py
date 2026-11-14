"""Mobile product-card rendering against the built app with synthetic data."""
import json,os,shutil,subprocess,sys,time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_workspace_ui import fixture

def main():
    data=fixture()
    data['history']=[{'id':'shopping-result','role':'assistant','status':'complete',
        'content':'Найдены проверенные варианты. Наличие 42 RU не подтверждено.','artifacts':[],
        'products':[{'title':title,'price':price,'currency':'RUB','url':'https://shop.example/product/'+str(i),
                     'checked_at':'2026-11-14T15:00:00Z'} for i,(title,price) in enumerate([('Тестовые низкие кеды','7990'),('Тестовые высокие кеды','6369')])]}]
    def route_api(route):
        path=urlparse(route.request.url).path
        if path=='/api/auth/me':body=data['user']
        elif path=='/api/bootstrap':body=data
        elif path=='/api/health':body={'status':'ok','runtime':{'contract':2},'restart_required':False}
        elif path.endswith('/model-settings'):body={'provider':'mock','model_name':'preview','override':False}
        else:route.fulfill(status=404,json={'detail':'Optional fixture endpoint unavailable'});return
        route.fulfill(status=200,json=body)
    output=ROOT/'.local'/'shopping-ui';output.mkdir(parents=True,exist_ok=True)
    url='http://127.0.0.1:4184'
    with (output/'preview.log').open('w') as log:
        server=subprocess.Popen([shutil.which('node'),str(ROOT/'frontend/node_modules/vite/bin/vite.js'),
            'preview','--host','127.0.0.1','--port','4184','--strictPort'],cwd=ROOT/'frontend',stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
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
                page=context.new_page();page.goto(url+'/?view=chat&thread=chat-0')
                products=page.get_by_role('region',name='Найденные товары')
                expect(products).to_be_visible()
                expect(products.locator('article')).to_have_count(2)
                expect(products.get_by_text('Нужный размер и доставку уточняйте в магазине.')).to_have_count(2)
                expect(products.locator('a')).to_have_count(2)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(output/'mobile.png'),full_page=True)
                browser.close()
            print(json.dumps({'status':'passed','products':2,'mobile_width':390}))
        finally:server.terminate();server.wait(timeout=10)

if __name__=='__main__':main()
