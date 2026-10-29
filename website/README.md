# Avelin public site prototype

Отдельный buildless prototype для AVE-57. Он не импортирует код рабочего `frontend` и не требует npm-зависимостей.

```powershell
.\.venv\Scripts\python.exe -m http.server 4180 --directory website
```

Открой `http://127.0.0.1:4180`. Download определяет устройство, оставляет платформу под контролем пользователя и показывает честное состояние Coming soon до публикации приложений.

## CTA configuration

`site-config.js` — единственное место для store URLs и маршрута web-auth. Платформа остаётся `enabled: false`, пока её публичная ссылка не проверена; вместо битого магазина посетитель попадёт на локальную `download.html`.

Auth выключен до появления production-аутентификации. После включения `main.js` передаёт только same-origin `returnTo` и отклоняет внешние адреса. CTA-события существуют только как браузерные `CustomEvent` с названием, платформой и признаком доступности — без персональных данных и сетевой аналитики.
