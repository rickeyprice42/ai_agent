from __future__ import annotations

from datetime import datetime

from ai_agent.executor import ExecutionEngine
from ai_agent.memory import MemoryStore
from ai_agent.planner import Planner
from ai_agent.tasks import TaskManager
from ai_agent.tools.base import Tool, ToolRegistry
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.shell import ShellSandbox
from ai_agent.types import Task


def register_builtin_tools(
    registry: ToolRegistry,
    memory: MemoryStore,
    tasks: TaskManager,
    planner: Planner,
    files: FileSandbox,
    shell: ShellSandbox,
    http: HttpSandbox,
    executor: ExecutionEngine,
) -> None:
    registry.register(
        Tool(
            name="get_time",
            description="Показывает текущие локальные дату и время.",
            schema={"type": "object", "properties": {}},
            handler=lambda _: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    )

    registry.register(
        Tool(
            name="remember_note",
            description="Сохраняет заметку в памяти агента.",
            schema={
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Текст заметки"},
                },
                "required": ["note"],
            },
            handler=lambda args: memory.add_note(str(args.get("note", ""))),
        )
    )

    registry.register(
        Tool(
            name="recall_notes",
            description="Возвращает сохраненные заметки пользователя.",
            schema={"type": "object", "properties": {}},
            handler=lambda _: _format_notes(memory),
        )
    )

    registry.register(
        Tool(
            name="read_file",
            description=(
                "Читает текстовый файл из безопасной рабочей папки инструментов. "
                "Принимает только относительный путь внутри этой папки."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь к файлу внутри рабочей папки инструментов",
                    },
                },
                "required": ["path"],
            },
            handler=lambda args: files.read_file(str(args.get("path", ""))),
        )
    )

    registry.register(
        Tool(
            name="write_file",
            description=(
                "Записывает текстовый файл в безопасную рабочую папку инструментов. "
                "Принимает только относительный путь внутри этой папки. "
                "По умолчанию не перезаписывает существующие файлы."
            ),
            schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь к файлу внутри рабочей папки инструментов",
                    },
                    "content": {"type": "string", "description": "Текст, который нужно записать"},
                    "overwrite": {
                        "type": "boolean",
                        "description": "Можно ли перезаписать файл, если он уже существует",
                    },
                },
                "required": ["path", "content"],
            },
            handler=lambda args: files.write_file(
                relative_path=str(args.get("path", "")),
                content=str(args.get("content", "")),
                overwrite=bool(args.get("overwrite", False)),
            ),
        )
    )

    registry.register(
        Tool(
            name="run_shell",
            description=(
                "Запускает ограниченную shell-команду внутри безопасной рабочей папки инструментов. "
                "Разрешены только команды из allowlist: python -m compileall, python -m pytest, "
                "npm test, npm run build, git status --short, dir, ls."
            ),
            schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Команда из allowlist, без shell-операторов и перенаправлений",
                    },
                },
                "required": ["command"],
            },
            handler=lambda args: shell.run(str(args.get("command", ""))),
        )
    )

    registry.register(
        Tool(
            name="http_request",
            description=(
                "Выполняет безопасный HTTP-запрос. Разрешены только GET и HEAD, "
                "только http/https URL, с таймаутом, лимитом ответа и блокировкой локальных сетей по умолчанию."
            ),
            schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP или HTTPS URL"},
                    "method": {"type": "string", "description": "GET или HEAD"},
                    "headers": {
                        "type": "object",
                        "description": "Необязательные HTTP headers без Authorization/Cookie",
                    },
                },
                "required": ["url"],
            },
            handler=lambda args: http.request(
                url=str(args.get("url", "")),
                method=str(args.get("method", "GET")),
                headers=args.get("headers") if isinstance(args.get("headers"), dict) else None,
            ),
        )
    )

    registry.register(
        Tool(
            name="execute_next_step",
            description=(
                "Выполняет следующий running-шаг активной задачи или запускает следующую задачу из очереди. "
                "Сам выбирает безопасный инструмент для известных типов шагов, пишет action log и обновляет статусы."
            ),
            schema={"type": "object", "properties": {}},
            handler=lambda _: executor.execute_next_step(),
        )
    )

    registry.register(
        Tool(
            name="create_task",
            description="Создает внутреннюю задачу Avelin с приоритетом и необязательными шагами.",
            schema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Что нужно сделать"},
                    "priority": {
                        "type": "integer",
                        "description": "Приоритет от 1 до 5, где 1 самый высокий",
                    },
                    "steps": {
                        "type": "array",
                        "description": "Начальный список шагов выполнения",
                    },
                },
                "required": ["description"],
            },
            handler=lambda args: _create_task(tasks, args),
        )
    )

    registry.register(
        Tool(
            name="plan_task",
            description="Планирует задачу: разбивает цель на шаги и сохраняет ее в очереди задач.",
            schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Цель или описание задачи"},
                    "priority": {
                        "type": "integer",
                        "description": "Приоритет от 1 до 5, где 1 самый высокий",
                    },
                },
                "required": ["goal"],
            },
            handler=lambda args: _plan_task(planner, tasks, args),
        )
    )

    registry.register(
        Tool(
            name="list_tasks",
            description="Показывает текущую очередь задач пользователя.",
            schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Сколько задач показать"},
                },
            },
            handler=lambda args: _format_tasks(tasks.list_tasks(limit=int(args.get("limit", 20)))),
        )
    )

    registry.register(
        Tool(
            name="start_next_task",
            description=(
                "Запускает следующую задачу из очереди: переводит задачу в executing "
                "и первый pending-шаг в running."
            ),
            schema={"type": "object", "properties": {}},
            handler=lambda _: _start_next_task(tasks),
        )
    )

    registry.register(
        Tool(
            name="add_task_step",
            description="Добавляет шаг к существующей задаче.",
            schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID задачи"},
                    "description": {"type": "string", "description": "Описание шага"},
                },
                "required": ["task_id", "description"],
            },
            handler=lambda args: _format_task(
                tasks.add_step(
                    task_id=str(args.get("task_id", "")),
                    description=str(args.get("description", "")),
                )
            ),
        )
    )

    registry.register(
        Tool(
            name="update_task",
            description="Меняет статус задачи и сохраняет результат, если он есть.",
            schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID задачи"},
                    "status": {
                        "type": "string",
                        "description": "created, planned, executing, completed или failed",
                    },
                    "result": {"type": "string", "description": "Итог или причина ошибки"},
                },
                "required": ["task_id", "status"],
            },
            handler=lambda args: _format_task(
                tasks.update_task(
                    task_id=str(args.get("task_id", "")),
                    status=str(args.get("status", "")),
                    result=str(args.get("result", "")),
                )
            ),
        )
    )

    registry.register(
        Tool(
            name="update_task_step",
            description="Меняет статус шага задачи и сохраняет наблюдение или результат.",
            schema={
                "type": "object",
                "properties": {
                    "step_id": {"type": "string", "description": "ID шага"},
                    "status": {
                        "type": "string",
                        "description": "pending, running, completed, failed или skipped",
                    },
                    "result": {"type": "string", "description": "Результат шага"},
                },
                "required": ["step_id", "status"],
            },
            handler=lambda args: _format_task(
                tasks.update_step(
                    step_id=str(args.get("step_id", "")),
                    status=str(args.get("status", "")),
                    result=str(args.get("result", "")),
                )
            ),
        )
    )


def _format_notes(memory: MemoryStore) -> str:
    notes = memory.list_notes()
    if not notes:
        return "В памяти пока ничего нет."
    formatted = "\n".join(f"{index}. {note}" for index, note in enumerate(notes, start=1))
    return f"Вот что я помню:\n{formatted}"


def _create_task(tasks: TaskManager, args: dict) -> str:
    raw_steps = args.get("steps", [])
    steps = [str(step) for step in raw_steps if str(step).strip()] if isinstance(raw_steps, list) else []
    task = tasks.create_task(
        description=str(args.get("description", "")),
        priority=int(args.get("priority", 3)),
        steps=steps,
    )
    return _format_task(task)


def _plan_task(planner: Planner, tasks: TaskManager, args: dict) -> str:
    plan = planner.create_plan(
        goal=str(args.get("goal", "")),
        priority=int(args.get("priority", 3)),
    )
    task = tasks.create_task(
        description=plan.goal,
        priority=plan.priority,
        steps=plan.steps,
    )
    task = tasks.update_task(task.id, "planned")
    return f"План создан и добавлен в очередь.\n{_format_task(task)}"


def _start_next_task(tasks: TaskManager) -> str:
    task = tasks.start_next_task()
    if task is None:
        return "В очереди нет задач, которые можно запустить."

    running_steps = [step for step in task.steps if step.status == "running"]
    if running_steps:
        current_step = running_steps[0]
        return (
            "Задача запущена.\n"
            f"{_format_task(task)}\n"
            f"Текущий шаг: {current_step.description}"
        )
    return f"Задача запущена.\n{_format_task(task)}"


def _format_tasks(items: list[Task]) -> str:
    if not items:
        return "В очереди задач пока пусто."
    return "\n\n".join(_format_task(task) for task in items)


def _format_task(task: Task) -> str:
    lines = [
        f"Задача {task.id}",
        f"Статус: {task.status}",
        f"Приоритет: {task.priority}",
        f"Описание: {task.description}",
    ]
    if task.result:
        lines.append(f"Результат: {task.result}")
    if task.steps:
        lines.append("Шаги:")
        for step in task.steps:
            result = f" -> {step.result}" if step.result else ""
            lines.append(f"{step.position}. [{step.status}] {step.description} (step_id: {step.id}){result}")
    return "\n".join(lines)
