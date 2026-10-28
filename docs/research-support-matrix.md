# Browser Research support matrix

`research_read` не обещает универсальный доступ к любому сайту. Он работает только
с публичными страницами без пользовательской сессии и возвращает evidence вместе с
URL, временем чтения, locale/region, confidence и SHA-256 каждого цитируемого фрагмента.

| Тип источника | Поддержка | Поведение |
|---|---|---|
| Статический HTML, `main`/`article` | Да | Bounded HTTP, очистка навигации и cookie boilerplate |
| JSON и JSON-LD | Да | Структурированные поля сохраняются в evidence |
| Таблицы и списки | Да | Извлекаются отдельно от основного текста |
| SPA/JavaScript | Ограниченно | Изолированный headless Edge fallback; иначе `unsupported_dynamic_page` |
| Apple Books | Adapter | Только прямая карточка `books.apple.com/<region>/book/...` |
| Apple TV | Adapter | Только прямая карточка `tv.apple.com/<region>/movie|show/...` |
| Apple TV+ availability | Fail closed | Нужна явная поддержка подписки на самой карточке |
| Login / личная сессия | Нет | `requires_login`; cookies и пароли пользователя недоступны |
| Paywall | Нет | `paywall` |
| CAPTCHA / anti-bot | Нет | `anti_bot`, без обхода защиты |
| Robots / legal policy | Нет | `robots_or_policy` |
| Private/local network | Нет | `private_network_blocked` до запроса |
| Файлы и бинарные downloads | Нет | `download_blocked` |
| Ответ сверх лимита | Нет | `response_too_large` |

Всё содержимое страницы считается недоверенными данными. Обнаруженная prompt
injection помечается в `safety.prompt_injection_detected`, не меняет план агента и
не даёт странице права вызывать инструменты.
