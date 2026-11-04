"""Public-safe follow-up report; private conversations/photo remain in ignored .local."""
from pathlib import Path
from datetime import datetime, timezone
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/followup-2026-11-02"


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


def e(value):
    return html.escape(str(value))


def main():
    modules = read(ROOT / ".local/audit/suites/results.json")["results"]
    assert all(row["status"] == "PASS" for row in modules)
    draft_log = (EVIDENCE / "draft-final.log").read_text(encoding="utf8")
    assert "Ran 7 tests" in draft_log and re.search(r"\bOK\s*$", draft_log)
    modules = [dict(row, tests=7) if row["module"] == "tests.test_chat_drafts" else row for row in modules]
    total = sum(row["tests"] for row in modules)
    ui = read(ROOT / ".local/workspace-review/results.json")
    assert ui["status"] == "passed"
    live = read(EVIDENCE / "watch-live-result-private.json")
    assert live["status"] == "COMPLETE" and "предел шагов" not in live["answer"].lower()
    vision = next(action for action in live["actions"] if action["tool_name"] == "analyze_image")
    vision_result = json.loads(vision["result"])
    searches = [action for action in live["actions"] if action["tool_name"] == "web_search"]
    assert "SWISSMATIC" in vision_result["text"] and vision_result["provider"] == "openai"
    preservation = read(EVIDENCE / "preservation.json")
    assert all(row["status"] == "PASS" for row in preservation)
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), tests=total,
        modules=len(modules), ui_scenarios=len(ui["checks"]), layout_checks=130,
        beam=dict(seconds=live["duration_s"], searches=len(searches), vision_provider=vision_result["provider"],
                  vision_model=vision_result["model"], step_exhaustion=False, exact_reference_and_price="unconfirmed"),
        preservation=preservation, results=modules)
    (EVIDENCE / "verification.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    tests = "".join(f'<tr><td>{e(row["module"])}</td><td>{row["tests"]}</td><td>PASS</td></tr>' for row in modules)
    content = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Avelin: часы, память и контекст — 2 ноября 2026</title>
<style>body{{margin:0;background:#101113;color:#e9ecf1;font:16px/1.65 system-ui,sans-serif}}main{{max-width:1000px;padding:32px 20px 70px;margin:auto}}h1{{font-size:clamp(28px,5vw,42px);line-height:1.2}}h2{{margin-top:38px;font-size:25px}}h3{{font-size:18px}}a{{color:#9cc4ff}}.muted,small{{color:#acb4c1}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:14px}}.card,details{{background:#191c21;border:1px solid #30343c;border-radius:14px;padding:20px}}strong.metric{{font-size:28px;display:block}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{border-bottom:1px solid #30343c;padding:12px 9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}.table{{overflow:auto}}.pass{{color:#87d6a1}}.partial{{color:#e9c484}}summary{{cursor:pointer}}pre{{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}}p,li{{max-width:90ch}}code{{overflow-wrap:anywhere}}@media(max-width:600px){{main{{padding:24px 14px}}td,th{{font-size:12px;padding:9px 6px}}}}</style>
<main><p class="muted">Avelin Core · 2 ноября 2026 · без commit/push</p><h1>Часы на фото, пустые диалоги, память и контекст</h1>
<p>Проверена настоящая история обоих аккаунтов, затем выполнены изолированные регрессии и повтор запроса через beam. История, заметки и настройки рабочих аккаунтов сохранены без изменений: сверены полные строки шести таблиц с начальным SQLite-снимком, включая 150 сообщений и 27 чатов. Личное фото и полный текст переписок в этот отчёт не включены.</p>
<div class="cards"><div class="card"><strong class="metric">{total} PASS</strong><small>уникальных тестов в {len(modules)} модулях</small></div><div class="card"><strong class="metric">{len(ui['checks'])} / 130</strong><small>UI-сценариев / layout-проверок</small></div><div class="card"><strong class="metric">11 → {len(searches)}</strong><small>поисковых запросов в реальном сценарии с часами</small></div></div>
<h2>Часы: почему закончились шаги</h2><p>Это был лимит цикла инструментов, а не нехватка контекста. Фото сначала обрабатывала Gemma 4, хотя основным собеседником был beam. Gemma предположила Everytime и пропустила читаемую надпись SWISSMATIC и окно даты. Затем beam сделал 11 вариантов поиска и ни разу не открыл страницу. Фильтр требовал цену уже в сниппете и отбрасывал полезные URL; совпадение по Chrono24 и общим словам пропустило нерелевантные Rolex/Breitling. Изменённые формулировки обходили прежнюю проверку повторных вызовов.</p>
<ul><li>Цена больше не обязательна для обнаружения URL. Цена подтверждается на самой странице.</li><li>Названия бренда/серии, явные кавычки и буквенно-цифровые референсы участвуют в проверке релевантности.</li><li>После трёх поисков без чтения агент получает фазу проверки источников. Четвёртый обходной поиск не вызывает сеть; последняя итерация зарезервирована под итоговый ответ.</li><li>Провайдер, игнорирующий финальную фазу, не может выполнить запоздалую запись файла. Полученное описание фото сохраняется и при резервном завершении.</li><li>Для мелких надписей и конкретной модели доступен <code>analyze_image detail=precise</code>: он предпочитает настроенный OpenAI vision. Обычный Gemma-маршрут не изменён; резервирование сохранено.</li></ul>
<div class="table"><table><thead><tr><th>Проверка</th><th>Итог</th><th>Доказательство</th></tr></thead><tbody><tr><td>Повтор того же пользовательского запроса на beam</td><td class="pass">PASS</td><td>{live['duration_s']} с, {len(searches)} поисковых запроса, нормальный итог вместо сообщения о лимите.</td></tr><tr><td>Чтение маркировки и даты</td><td class="pass">PASS</td><td>{e(vision_result['model'])}: TISSOT, 1853, SWISSMATIC, окно даты у «3». Сверено с исходным фото.</td></tr><tr><td>Точный референс, подлинность и актуальная цена</td><td class="partial">НЕ ПОДТВЕРЖДЕНО</td><td>Внешний вид и надпись не доказывают конкретную модификацию. Агент не выдал гипотезу за подтверждённый артикул или цену. Для точного сопоставления полезна маркировка задней крышки.</td></tr></tbody></table></div>
<details><summary>Фактический итог beam после исправлений</summary><pre>{e(live['answer'])}</pre></details>
<h2>Пустые чаты</h2><p>Повторный «Новый диалог» безусловно вставлял новую строку в БД. Теперь UI запрашивает атомарное переиспользование пустого черновика того же владельца и проекта. У нового пользователя переиспользуется также его канонический начальный Main conversation. Старые неактивные автоматические пустые записи скрыты из личной истории и счётчика; текущий черновик, именованные, закреплённые и общие комнаты сохранены. После первого сообщения чат попадает в историю. Физического удаления старых записей нет.</p><p>Проверено 7 тестами, включая 12 параллельных запросов, изоляцию пользователей/проектов, первое сообщение и отсутствие второго начального черновика. UI также проверяет старые пустые строки и сохранение именованных/закреплённых чатов. Ограничение старой схемы: намеренно названный ровно «New chat» диалог нельзя отличить от автоматически названного; сохранённые данные всё равно не удаляются.</p>
<h2>Что происходит с памятью ваших аккаунтов</h2><div class="table"><table><thead><tr><th>Аккаунт</th><th>Фактическое состояние</th><th>Почему сводка выглядит так</th></tr></thead><tbody><tr><td>iPhone / iphonetest</td><td>Автосбор включён. Записей и кандидатов: 0.</td><td>Единственный подходящий для текущего extractor факт был сообщён 29 октября в 12:40:51 UTC, а память включена в 12:49:41 UTC — спустя девять минут. Старые чаты не импортируются автоматически. В более поздних сообщениях текущий узкий extractor не нашёл новых устойчивых фактов.</td></tr><tr><td>rickey</td><td>Автосбор выключен. Есть одна активная ручная запись.</td><td>Отключение автосбора не отключает просмотр и использование ручной памяти. Она должна оставаться видимой в сводке.</td></tr></tbody></table></div>
<p>Нынешняя сводка — представление уже сохранённых структурированных записей, а не генерация нового рассказа о пользователе из всей истории. Кнопка теперь называется «Обновить список», интерфейс объясняет пустое состояние и режим автосбора; пустая сводка больше не показывает ложное «обновлено сейчас».</p>
<h3>Найденные и исправленные ошибки памяти</h3><ul><li>Отредактированная, удалённая, отозванная или просроченная запись могла возвращаться через старую копию notes. Закрыты retrieval, recall и bootstrap snapshot.</li><li>Один и тот же completed turn считался несколькими независимыми наблюдениями. Теперь повтор обработки не повышает подтверждение факта.</li><li>Первые подтверждающие сообщения теряли provenance; сохранены все уникальные источники.</li><li>Отозванный автоматический факт мог создаваться заново без нового разрешения. Это поведение остановлено.</li><li>Содержимое вложения могло стать фактом о пользователе. Извлечение использует настоящий пользовательский текст из доверенной метаинформации; исходное содержимое файла остаётся данными контекста.</li></ul>
<h2>Контекст и сжатие</h2><p>В сохранённых traces обоих аккаунтов <code>omitted_message_count=0</code> и <code>overflow=false</code>. Полная история наиболее длинных отдельных чатов оценивалась примерно в 4,9–5,2 тысячи токенов при входном бюджете 28 672. С учётом других компонентов сохранённые traces также не требовали сжатия; ноль циклов здесь ожидаем.</p><p>Сжатие отдельно испытано на длинном синтетическом диалоге со 102 сообщениями. Исправлены потеря начальной цели и использование подтверждённого снимка после изменения его источников. Историческая выдержка цели содержит ссылку на сообщение и явно уступает новым указаниям. Текст документа больше не превращается в закреплённый запрет пользователя. Проверены исходные сообщения, review, rollback, конфликты, restart и понятная ошибка при невозможности вместить обязательные данные.</p>
<h2>Граница готовности</h2><p><strong>Это ещё не автономная память и сжатие уровня ChatGPT.</strong> Автосбор пока использует консервативные русские шаблоны и ограниченный набор явных фактов; предпочтения требуют независимого повторения. Неявные интересы и свободные формулировки он понимает ограниченно. Retrieval лексический, сводка группирует записи, а compaction извлекает выдержки и требует проверки перед заменой исходного контекста. Следующий отдельный этап — модельное извлечение фактов с источниками, семантическая релевантность, связная сводка и безопасная автоматическая compaction. Старые личные чаты в память молча не импортировались.</p>
<h2>Проверка перед завершением</h2><p>Полный изолированный прогон: 228 тестов PASS. После уточнения начального пустого чата повторно прошёл модуль черновиков из 7 тестов; итоговый проверенный состав — {total} уникальных тестов в {len(modules)} модулях. Frontend build и {len(ui['checks'])} UI-сценариев с 130 layout-проверками прошли. Физическая iOS Safari не автоматизировалась. Реальный beam-прогон использовал отдельные SQLite и workspace, рабочие аккаунты не изменены.</p>
<details><summary>Модули и количество тестов</summary><div class="table"><table><thead><tr><th>Модуль</th><th>Тестов</th><th>Итог</th></tr></thead><tbody>{tests}</tbody></table></div></details>
<p class="muted">Без commit/push и изменений Linear. Производственные серверы и приватный доступ не запускались. Все приватные исходные доказательства — в игнорируемой .local. Предыдущий этап: <a href="avelin-product-improvements-2026-11-02.html">чат, группы, разрешения и Apple</a>.</p></main></html>'''
    target = ROOT / "reports/qa/avelin-watch-memory-followup-2026-11-02.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf8")
    print(json.dumps(dict(report=str(target), tests=total, modules=len(modules), ui=len(ui["checks"])), ensure_ascii=False))


if __name__ == "__main__":
    main()
