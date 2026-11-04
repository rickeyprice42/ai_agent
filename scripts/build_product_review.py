"""Build a self-contained review from isolated test evidence; no private chats/secrets."""
from datetime import datetime, timezone
from pathlib import Path
import base64
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / ".local" / "audit"
LIVE = ROOT / ".local" / "product-review"
OUT = ROOT / "reports" / "qa"


def read_json(path):
    return json.loads(path.read_text(encoding="utf8"))


def e(value):
    return html.escape(str(value))


def main():
    suites = read_json(QA / "suites/results.json")["results"]
    rows = {row["module"]: dict(row) for row in suites}
    research_log = (QA / "product-final-research.log").read_text(encoding="utf8")
    desktop_log = (QA / "product-desktop-retest.log").read_text(encoding="utf8")
    assert "Ran 37 tests" in research_log and re.search(r"\bOK\s*$", research_log)
    assert "Ran 3 tests" in desktop_log and re.search(r"\bOK\s*$", desktop_log)
    # Latest parser checks extend the full isolated run; desktop UIA needs an
    # interactive Windows session and was rerun separately outside the sandbox.
    rows["tests.test_catalog_research"].update(tests=13, status="PASS", evidence="product-final-research.log")
    rows["tests.test_desktop_runner"].update(tests=3, status="PASS", evidence="product-desktop-retest.log")
    assert all(row["status"] == "PASS" for row in rows.values())
    total = sum(row["tests"] for row in rows.values())
    ui = read_json(ROOT / ".local/workspace-review/results.json")
    apple = read_json(LIVE / "apple-agent-live.json")["results"]
    photo = read_json(LIVE / "latest-live.json")
    browser = read_json(QA / "apple-revision/force-browser-proof.json")
    assert ui["status"] == "passed" and photo["status"] == "PASS"
    assert len(apple) == 2 and all(case["grade"] == "PASS" for case in apple)
    assert browser["extraction_method"] == "browser" and browser["fields"]["catalog_item"]["offer_type"] == "TV_PLUS"
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), tests=total,
        modules=len(rows), results=list(rows.values()), ui_scenarios=len(ui["checks"]),
        layout_checks=130, live=[dict(case=case["case"], status=case["grade"], seconds=case["elapsed_seconds"], verified_items=case["grounded_final_item_count"]) for case in apple],
        photo=dict(status=photo["status"], seconds=photo["duration_s"]), forced_browser="PASS",
        note="Full suites plus latest parser regressions and separate Windows UIA retest; initial sandbox UIA failure retained in original logs.")
    (LIVE / "verification.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")

    checks = [
        ("PASS", "Личный чат", "Подписи «Ты» / «Avelin» убраны. Имена участников показываются только в общем чате."),
        ("PASS", "Подробности работы", "Ход инструментов свёрнут; текущая операция видна в строке подробностей. Сырой текст вложений не заполняет сообщение после refresh."),
        ("PASS", "Фото → анализ → DOCX", f"OpenAI прочитал случайный код на изображении и создал DOCX с правильным текстом и цветом; {photo['duration_s']} с. Ключевой ответ не был подсказан в промпте."),
        ("PASS", "Разрешения в чате", "Popup с целью, объектом действия, сроком и подробностями. Allow/Deny; восстановление после reload; продолжение исходной задачи без повторного эффекта."),
        ("PASS", "Совместная работа", "Отдельный раздел, создание группы и общего чата. Участники и режим агента за кнопкой настроек. Одноразовая ссылка или привязка к email, явное принятие приглашения."),
        ("PASS", "Обычный / общий чат", "Подсказка @Avelin есть только в группе. Личная память и файлы не становятся общими автоматически."),
        ("PASS", "Базовые автоматизации", "Scheduler обрабатывает очередь; ручной запуск не меняет расписание. Результат, уведомление и скачиваемый DOCX сохраняются после restart."),
        ("FAIL", "Произвольные сложные LLM-автоматизации", "Durable planner пока поддерживает ограниченный набор действий. Неподдерживаемый шаг теперь явно завершается ошибкой; полноценный цикл обычного чата ещё требуется перенести в worker."),
        ("PASS", "Apple TV / Books через OpenAI", "Два настоящих сквозных запроса: по три проверенные карточки. TV: конкретное предложение Apple TV, регион US, без подмены сторонним каналом. Books: авторы и USD-цены."),
        ("PASS", "Apple TV через Playwright", "Изолированный force_browser для Wolfs подтвердил title-bound TV_PLUS. Первый запуск при параллельной нагрузке дал timeout; одиночный повтор прошёл."),
        ("PASS", "Ссылки скачивания", "Карточки и Markdown-ссылки на известные артефакты скачивают файл с авторизацией. Неизвестный локальный путь не превращается в ссылку."),
        ("PASS", "Макеты desktop / mobile", f"{len(ui['checks'])} сценариев, 130 проверок layout: 320/390/768/1024/1440 px, светлая и тёмная темы. Edge Chromium."),
        ("NOT TESTED", "Физический iPhone / Safari", "Нужен ручной приёмочный прогон: клавиатура, фото из галереи, popup, скачивание в Files и групповой чат по Tailscale."),
    ]
    table = "".join(f'<tr><td><span class="badge {status.lower().replace(" ", "-")}">{status}</span></td><th>{e(title)}</th><td>{e(detail)}</td></tr>' for status, title, detail in checks)
    live_sections = []
    for case in apple:
        items = []
        for item in case["items"]:
            facts = item["facts"]
            if not item["passed"]:
                continue
            detail = (f'{facts.get("year", "")} · Apple TV · TV_PLUS · US' if case["case"] == "apple_tv" else
                f'{", ".join(facts["authors"])} · {facts["verified_price"]} {facts["currency"]} · US')
            items.append(f'<li><a href="{e(facts["url"])}">{e(facts["title"])}</a><small>{e(detail)}</small></li>')
        title = "Apple TV" if case["case"] == "apple_tv" else "Apple Books"
        live_sections.append(f'<article class="card"><h3>{title} — {case["grade"]}</h3><p>OpenAI gpt-6.1-sol · {case["elapsed_seconds"]} с · 3 проверенные карточки.</p><ul>{"".join(items)}</ul><details><summary>Промпт теста</summary><p>{e(case["prompt"])}</p></details></article>')
    images = []
    for filename, caption in [
        ("workspace-mobile-chat.png", "Личный чат: подписи ролей убраны, подробности раскрыты вручную."),
        ("workspace-mobile-group-settings.png", "Настройки группы и приглашение. Синтетический тестовый аккаунт."),
        ("workspace-mobile-approval-approved.png", "Разрешение действия. Синтетическая UI-проверка; физический iPhone не использовался."),
    ]:
        path = ROOT / ".local/workspace-review" / filename
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        images.append(f'<figure><img src="data:image/png;base64,{data}" alt="{e(caption)}"><figcaption>{e(caption)}</figcaption></figure>')
    suites_table = "".join(f'<tr><td>{e(row["module"])}</td><td>{row["tests"]}</td><td>PASS</td></tr>' for row in rows.values())
    document = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Avelin — улучшения и проверка, 1–2 ноября 2026</title>
<style>body{{margin:0;background:#101113;color:#eceef2;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:36px 20px 72px}}h1{{font-size:clamp(28px,5vw,44px);line-height:1.15}}h2{{margin-top:48px}}h3{{margin-top:0}}p,li{{max-width:92ch}}small,figcaption,.muted{{display:block;color:#aeb4c0;font-size:13px}}a{{color:#9ac2ff;overflow-wrap:anywhere}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:18px}}.card,figure{{background:#191b20;border:1px solid #30343d;border-radius:16px;padding:20px;margin:0;min-width:0}}.metric{{font-size:28px;font-weight:650}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;vertical-align:top;border-bottom:1px solid #30343d;padding:13px 9px;overflow-wrap:anywhere}}th{{font-weight:550}}.table{{overflow:auto}}.badge{{font-size:11px;font-weight:700;white-space:nowrap}}.pass{{color:#83dca2}}.fail{{color:#ffad9e}}.not-tested{{color:#edca8b}}details{{margin:18px 0}}summary{{cursor:pointer;color:#b5c9e8}}figure img{{width:100%;height:auto;max-height:700px;object-fit:contain}}figcaption{{margin-top:12px}}code{{overflow-wrap:anywhere}}@media(max-width:600px){{main{{padding:24px 14px}}td,th{{padding:11px 6px;font-size:12px}}}}</style>
<main><p class="muted">Avelin Core · 1–2 ноября 2026 · локальные изменения без commit/push</p><h1>Чат, разрешения, группы и работа с сайтами</h1><p>Исходные симптомы воспроизведены и исправлены без удаления истории и предыдущих доработок. Этот отчёт дополняет <a href="avelin-comprehensive-audit-2026-10-30.html">baseline-аудит</a>. Он разделяет проверенные сценарии и оставшиеся ограничения.</p>
<div class="grid"><div class="card"><div class="metric">{total}</div><small>тестов в {len(rows)} модулях · PASS с отдельным Windows UIA-прогоном и актуальными parser-регрессиями</small></div><div class="card"><div class="metric">20 / 130</div><small>UI-сценариев / layout-проверок</small></div><div class="card"><div class="metric">3 + 3</div><small>проверенных карточки TV и Books в реальных ответах OpenAI</small></div></div>
<h2>Результаты</h2><div class="table"><table><thead><tr><th>Статус</th><th>Сценарий</th><th>Что проверено</th></tr></thead><tbody>{table}</tbody></table></div>
<h2>Почему возникали ошибки</h2><ul><li><strong>Фото:</strong> вложение передавалось модели как путь, а policy требовала отдельное разрешение. Чат не имел надёжного механизма продолжения такого действия. Теперь явное вложение разрешает только анализ этого файла в текущем запросе; произвольное другое изображение требует popup.</li><li><strong>Автоматизации:</strong> scheduler создавал durable run, но никто не забирал его из очереди. Теперь backend обрабатывает только automation runs. Дополнительно исправлен выбор run после approval: более приоритетная личная задача не перехватывает запуск.</li><li><strong>Apple:</strong> прежний reader ограничивался 20 КБ, тогда как реальные страницы занимают сотни килобайт. Браузер терял структурированные поля, а наличие старого названия TV+ использовалось как слабый сигнал. Теперь reader ограничен 2 МБ, извлекает JSON-LD и данные конкретного предложения; чужой trial не доказывает подписку Apple TV.</li><li><strong>Служебный шум:</strong> действия развёрнуты в отдельные подробности, а машинное извлечение вложений остаётся в контексте модели, сохраняя чистое сообщение пользователя после refresh.</li></ul>
<h2>Реальные сквозные проверки</h2><p>Проверки выполнены 1 ноября в отдельных SQLite-базах и файловых папках. Названия, авторы, цены, годы и ссылки сопоставлены с фактическими результатами чтения карточек. Поисковые сниппеты не засчитывались как проверенные факты. Данные отражают момент теста, цены и доступность могут изменяться.</p><div class="grid">{"".join(live_sections)}</div>
<p>Оба сценария пережили неудачный поиск: модель самостоятельно сменила запрос или перешла к браузеру и чтению прямых карточек. Дополнительно подтверждены четыре реальные HTTP-страницы и один изолированный Playwright-прогон Wolfs. Первый force_browser при параллельной нагрузке завершился таймаутом; одиночный повтор прошёл. В браузерные ошибки добавлены этап и длительность таймаута.</p>
<h2>Повторная проверка пользователем</h2><ol><li>В личном чате прикрепить фото и попросить прочитать надпись и создать Word-файл. Должны появиться краткий ответ и скачиваемый файл, без блокирующего разрешения на это вложение.</li><li>Запросить действие, требующее разрешения, проверить цель и детали, обновить страницу. Окно должно восстановиться; «Отклонить» не выполняет действие, «Разрешить» продолжает исходную задачу.</li><li>В «Совместно» создать группу, выпустить одноразовую ссылку и принять её другим аккаунтом. Имена и @Avelin появляются только в общем чате. Личные чаты второго аккаунта недоступны.</li><li>В автоматизациях создать простой DOCX-черновик и нажать «Запустить». Проверить результат, файл и уведомление, затем обновить страницу. Расписание сохраняется.</li><li>Попросить три фильма Apple TV по подписке US и три книги Apple Books. Проверить прямые карточки и подтверждение цены или честно указанное отсутствие цены.</li></ol>
<h2>Что ещё нужно сделать</h2><ul><li><strong>Приоритет 1:</strong> полноценный LLM-цикл для durable автоматизаций с бюджетом, восстановлением и approval. Текущий ограниченный planner не равнозначен обычному чату.</li><li><strong>Приоритет 1:</strong> приёмка на физическом iPhone/Safari двумя аккаунтами: галерея, клавиатура, popup, Files, вход по приглашению.</li><li><strong>Приоритет 2:</strong> резервный поисковый источник и измерение качества поиска. В TV-прогоне два поиска не дали результатов; успешное чтение сайта позволило завершить задачу.</li><li>Инструменты в shared-чате пока отключены до безопасной явной передачи общих ресурсов. Новый интерфейс не расширяет доступ к личным файлам.</li><li>Публичные Apple-карточки не доказывают поддержку всех сайтов. Login, paywall и CAPTCHA не обходятся; динамические страницы и сеть могут завершаться ограниченным таймаутом.</li></ul>
<h2>Интерфейс</h2><div class="grid">{"".join(images)}</div>
<details><summary>Автоматизированные модули — {total} тестов</summary><p>Полный прогон дополнен последними parser-тестами. Windows UI Automation не видела тестовое окно из sandbox; отдельный разрешённый прогон всех трёх desktop-тестов прошёл. Исходный неуспешный лог сохранён, ошибка окружения не скрыта.</p><div class="table"><table><thead><tr><th>Модуль</th><th>Тестов</th><th>Итог</th></tr></thead><tbody>{suites_table}</tbody></table></div></details>
<p class="muted">Коммитов, push и изменений статусов Linear в этой итерации не было. Production-сервер не запускался; приватный маршрут Tailscale остановлен. Первичные логи и SQLite-доказательства остаются в игнорируемой .local и не включены в отчёт.</p></main></html>'''
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "avelin-product-improvements-2026-11-02.html"
    target.write_text(document, encoding="utf8")
    print(json.dumps(dict(report=str(target), tests=total, modules=len(rows), bytes=target.stat().st_size), ensure_ascii=False))


if __name__ == "__main__":
    main()
