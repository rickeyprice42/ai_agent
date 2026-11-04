"""Public-safe release report assembled from completed, isolated QA evidence."""
from datetime import datetime, timezone
from html import escape
import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / '.local/product-2026-11-04'


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def passed_log(name, expected):
    text = (QA / name).read_text(encoding='utf8')
    assert re.search(r'\bOK\s*$', text) and f'Ran {expected} tests' in text, name


def tests_in(module):
    tree = ast.parse((ROOT / (module.replace('.', '/') + '.py')).read_text(encoding='utf8'))
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_') for node in ast.walk(tree))


def latest_live(base, provider):
    paths = sorted((ROOT / base / provider).glob('*/result.json'))
    assert paths, (base, provider)
    data = read(paths[-1])
    assert data['status'] == 'PASS', data.get('failure')
    return {key: data.get(key) for key in ['provider', 'model', 'elapsed_seconds', 'status', 'decisions']}


def main():
    suites = read(ROOT / '.local/audit/suites/results.json')['results']
    assert len(suites) == 40 and all(row['status'] == 'PASS' for row in suites)
    modules = {row['module']: dict(row) for row in suites}
    passed_log('media-office-final.log', 15)
    modules['tests.test_media_office_workflows']['tests'] = 15
    runtime_modules = ['tests.test_generated_extensions', 'tests.test_tool_factory', 'tests.test_runtime_audit',
        'tests.test_search_audit', 'tests.test_search_convergence', 'tests.test_provider_and_research_quality',
        'tests.test_chat_completion_notifications']
    runtime_count = sum(tests_in(module) for module in runtime_modules)
    passed_log('runtime-final-tests.log', runtime_count)
    for module in runtime_modules:
        modules[module]['tests'] = tests_in(module)
    total = sum(row['tests'] for row in modules.values())
    ui = read(ROOT / '.local/workspace-review/results.json')
    assert ui['status'] == 'passed' and len(ui['checks']) == 27
    live = [dict(kind=kind, **latest_live(base, provider)) for kind, base in [
        ('HEIC → PNG 256×128', '.local/product-2026-11-04/media-live'),
        ('Tool Factory: proposal → approval → tests → approval → JSON', '.local/followup-2026-11-04/generated-live')]
        for provider in ['openai', 'ollama_cloud']]
    cases = [
        ('HEIC и популярные изображения', 'PASS', 'HEIC/HEIF, JPEG/PNG/WebP/GIF/BMP/TIFF/AVIF: реальный decode, превью, нормализация для vision. Оригиналы сохраняются. 10 МБ / 40 Мп; первый кадр многостраничных файлов.'),
        ('Office с макросами', 'PASS', 'DOCM/XLSM/PPTM и macro templates: чтение, отдельная изменённая копия, побайтное сохранение VBA. Синтетические ZIP fixtures; реальный Excel/VBA запуск не выполнялся.'),
        ('Очередь отправленных вложений', 'PASS', 'Очищается после принятия HTTP-запроса до конца генерации. При 503 до принятия сохраняется вместе с черновиком.'),
        ('Превью отправленной фотографии', 'PASS', 'Авторизованное изображение, крупный просмотр, исходная загрузка и восстановление после чтения истории. Проверена изоляция аккаунтов.'),
        ('Enter на телефоне', 'PASS', 'Touch Enter — перенос, desktop Enter — отправка, Shift+Enter — перенос. Проверено в Chromium touch emulation; физический Safari отдельно.'),
        ('Логотип sidebar и Home Screen', 'PASS', 'Поставленные логотипы, manifest, 180/192/512px icons. Установку/обновление уже существующего значка проверяет владелец на iPhone.'),
        ('Размер/формат фотографий', 'PASS', 'Обе реальные модели вызвали transform_image; готовый PNG 256×128 доступен как artifact, hash исходного HEIC сохранился.'),
        ('Повторный запуск автоматизации', 'PASS', 'Причина локализована: одинаковый dated Word path. Теперь каждый run пишет в собственную область; три запуска за день сохраняют отдельные результаты.'),
        ('Web Push: backend и UI', 'PASS', 'Подписки, durable outbox/lease/retry, реальный AES128GCM+VAPID через mock transport, согласие, service worker, test/logout/unsubscribe. Прямой чат уведомляет только о завершении.'),
        ('Push на закрытом iPhone', 'NOT TESTED', 'Нужна реальная установка HTTPS-приложения на экран «Домой», нажатие «Включить уведомления» и проверка с заблокированным экраном. Это незавершённая приёмка AVE-63.'),
        ('Автоматические названия чатов', 'PASS', 'Мгновенное название темы и durable фоновый запрос модели без инструментов/памяти/вложений. Ошибка сохраняет fallback; ручные названия защищены от поздней перезаписи.'),
        ('Поиск', 'PASS', 'Повторяющиеся URL переводят модель к чтению; 3 поиска без чтения ограничены; ошибочные GTA V результаты/OR-якоря покрыты регрессиями. Это проверка исправлений, не гарантия нахождения любого NPC.'),
        ('Кнопка Copy кода', 'PASS', 'Копируется только текст кода; UI проверяет clipboard, доступность и отсутствие горизонтального переполнения.'),
        ('Аватар и кадрирование', 'PASS', 'HEIC и другие входы нормализуются, drag/zoom/keyboard crop, PNG 256×256, обновление sidebar и удаление. Auth/tenant tests проходят.'),
        ('Генерация инструментов и SKILL.md', 'PASS', 'Native proposal, обзорный ZIP, точный digest approval, затем contract tests; отдельное approval выполнения, JSON в outputs, owner/version/input cache. Реальные OpenAI и beam прошли весь путь.'),
        ('Произвольные тяжёлые Python-задачи', 'NOT TESTED', 'Текущий worker — ограниченные pure-data выражения без импортов/циклов/I/O. Для тяжёлых библиотек и обработчиков создана отдельная AVE-66 с OS isolation и quotas.'),
    ]
    issues = [
        ('AVE-62', 'Готово; физический Safari/Home Screen проверяется владельцем', 'Вложения, фотографии, мобильный интерфейс и аватар.'),
        ('AVE-63', 'In Review', 'Исправление автоматизаций готово; фактическая доставка push на iPhone ещё не принята.'),
        ('AVE-64', 'Готово', 'Search convergence, Tool Factory/Skills, approval, cache, реальные модели.'),
        ('AVE-65', 'Готово', 'Тематические названия новых чатов, фоновые jobs и защита ручных имён.'),
        ('AVE-66', 'Backlog', 'Изолированный runtime для тяжёлых сгенерированных программ.'),
        ('AVE-60 / AVE-61', 'Следующий этап', 'Сервер, хранение/backup/HTTPS, staging и безопасные обновления; затем нагрузочные и большие продуктовые тесты.'),
    ]
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'tests': total, 'modules': len(modules),
        'ui_scenarios': len(ui['checks']), 'layout_combinations': 130, 'service_worker_scenarios': 8,
        'live': live, 'cases': cases, 'module_results': list(modules.values()),
        'physical_iphone_push': 'NOT TESTED', 'production_server_started': False}
    (QA / 'release-validation.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(c)) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    body = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Avelin · проверка мобильного релиза</title>
<style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0c0d10;color:#f3f3f4;font:16px/1.65 system-ui,sans-serif}}main{{max-width:1140px;margin:auto;padding:48px 24px}}h1{{font-size:clamp(30px,5vw,50px);line-height:1.13;letter-spacing:-.04em}}h2{{margin-top:48px}}p{{max-width:900px;color:#bfc0c9}}a{{color:#8dbbff}}.stats{{display:flex;gap:12px;flex-wrap:wrap}}.stats b{{background:#191b22;border:1px solid #30333e;padding:16px 22px;border-radius:16px}}.scroll{{overflow:auto;border:1px solid #30333e;border-radius:14px}}table{{width:100%;border-collapse:collapse;min-width:620px}}th,td{{padding:14px 16px;text-align:left;vertical-align:top;border-bottom:1px solid #292b34}}th{{background:#191b22}}td:first-child{{font-weight:600}}code{{overflow-wrap:anywhere}}li{{margin:8px 0}}@media(max-width:500px){{main{{padding:28px 16px}}.stats b{{padding:12px}}}}</style>
<main><p>AVELIN CORE · 4 ноября 2026</p><h1>Живое использование:<br>мобильный чат, файлы и инструменты</h1>
<p>Проверены незавершённые запросы аккаунта iPhone и внесены исправления. История и личные вложения оставлены на компьютере. Протоколы ниже используют искусственные данные и изолированные тестовые БД.</p>
<div class="stats"><b>{total} тестов · PASS</b><b>{len(modules)} модулей</b><b>27 UI-сценариев</b><b>130 layouts</b><b>4 live прогона · PASS</b></div>
<h2>Проверки по запросу</h2>{table(['Сценарий','Статус','Результат и границы'], cases)}
<h2>Что ломалось и как воспроизвести</h2><ol>
<li><b>Автоматизация:</b> запустить датированный Word-шаблон дважды в день после scheduled run. Раньше повтор писал в тот же файл и падал на защите от overwrite. Теперь местоположение привязано к automation/run ID; предыдущие результаты сохраняются.</li>
<li><b>Модель не могла конвертировать изображение:</b> запросить HEIC→PNG с заданной шириной. В прежнем наборе не было соответствующего инструмента. Теперь есть локальный transform_image; две модели выдали проверяемый файл с нужными пикселями.</li>
<li><b>Пустой/длинный поиск:</b> реальный NPC-запрос перебирал GTA V/Wikipedia URL и угадывал страницы с403/404. Исправлены релевантность, OR-формулировки и переход к чтению при повторных URL. Референс/ID без доказательства не объявляется подтверждённым.</li>
<li><b>Generated tools:</b> обе реальные модели передали tests.arguments, а undocumented checker ожидал input; immutable rejected version блокировала исправление. Теперь typed schema, нормализация однозначного alias и конкретная следующая patch-version. После исправления обе модели прошли полный native workflow.</li>
<li><b>Опасные ложные успехи:</b> rejected draft, timeout после запуска кода, поздняя активация и изменённый cached artifact покрыты негативными регрессиями. Отклонённый draft может оставить обзорный ZIP, но не считается выполненной задачей и не создаёт success push.</li></ol>
<h2>Реальные модели</h2>{table(['Сценарий','Provider / model','Время, с','Статус'], [(x['kind'],x['provider']+' / '+x['model'],x['elapsed_seconds'],x['status']) for x in live])}
<p>В generated workflow обе модели: 0 запусков до первого подтверждения, затем 2 contract tests, отдельное подтверждение выполнения и проверенный JSON с42. Тестовый harness подтверждал только заранее проверенную искусственную операцию суммы19+23. Никаких production account изменений.</p>
<h2>Приёмка на iPhone</h2><ol><li>Запустить обновлённый launcher и private preview через Tailscale.</li><li>Открыть HTTPS-адрес, добавить Avelin на экран «Домой» с новым логотипом, запустить приложение с этого значка.</li><li>Настройки → Уведомления → Включить → разрешить → Проверить уведомление.</li><li>Закрыть PWA, заблокировать экран и дождаться завершения тестовой автоматизации. Проверить уведомление и переход в нужный диалог/результат.</li><li>Проверить HEIC, кадрирование и перенос строки настоящей клавиатурой Safari. Этот этап пока NOT TESTED.</li></ol>
<p>Backend и worker должны работать. Требования iOS описаны в <a href="https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/">официальном руководстве WebKit</a>. Серверные ключи подготовлены локально; private key, подписки и личные данные исключены из Git.</p>
<h2>Linear и следующий этап</h2>{table(['Задача','Состояние','Содержание'], issues)}
<p>План: короткая приёмка на телефоне → AVE-60 (сервер/хранение/резервные копии) → AVE-61 (обновления со staging и rollback) → нагрузочные проверки. Landing уже существует в AVE-57; его развитие следует согласовать отдельно, без повторения прежней задачи. AVE-66 расширяет тяжёлые программные сценарии после выбора runtime.</p>
<h2>Воспроизводимость</h2><ul><li>Полный исходный прогон:291 тест /40модулей. После финального review повторены изменённые runtime-модули и15 Office/media тестов; число уникальных текущих тестов —{total}.</li><li>Production build и27 UI-сценариев с130 responsive combinations;8 service-worker сценариев. UI использует mocks, Chromium/Edge — это не физический Safari.</li><li>Локальная SQLite-копия до запуска обновлённых workers создана, PRAGMA integrity_check=ok; manifest содержит SHA-256. Копия находится только в игнорируемом data/backups/.</li><li>Скрипты: audit_test_suites.py, verify_workspace_ui.py, verify_push_worker.mjs, verify_media_live.py и verify_generated_extensions_live.py. Последние два требуют ключей и обращаются к провайдерам на искусственных данных.</li></ul>
<p><a href="avelin-watch-memory-followup-2026-11-02.html">Предыдущий отчёт: поиск, память и контекст</a> · <a href="avelin-product-improvements-2026-11-02.html">Предыдущие продуктовые исправления</a></p></main></html>'''
    path = ROOT / 'reports/qa/avelin-mobile-release-review-2026-11-04.html'
    path.write_text(body, encoding='utf8')
    print(json.dumps({'report': str(path.relative_to(ROOT)), 'tests': total, 'modules': len(modules), 'live': len(live)}))


if __name__ == '__main__':
    main()
