# Avelin public site prototype

Отдельный buildless prototype для AVE-57. Он не импортирует код рабочего `frontend` и не требует npm-зависимостей.

```powershell
.\.venv\Scripts\python.exe -m http.server 4180 --directory website
```

Открой `http://127.0.0.1:4180`. Download и Auth показывают честное состояние Coming soon. Реальная маршрутизация платформ и безопасный auth redirect относятся к AVE-58.
