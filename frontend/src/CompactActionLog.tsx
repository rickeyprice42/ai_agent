import type { ActionLogItem } from "./types";
import { WorkspaceIcon, type IconName } from "./WorkspaceIcon";
import { panelTime } from "./panelApi";
import "./component-panels.css";

const names: Record<string,string> = {
  web_search:"Поиск в интернете", research_read:"Чтение источника", browser_read:"Просмотр страницы", browser_interact:"Работа в браузере", http_request:"Запрос к сайту",
  analyze_image:"Анализ изображения", inspect_image:"Проверка изображения", transform_image:"Обработка изображения",
  create_docx:"Создание документа Word", append_docx:"Дополнение документа", inspect_office_document:"Чтение документа", edit_office_document:"Редактирование документа", create_presentation:"Создание презентации",
  write_file:"Сохранение файла", read_file:"Чтение файла", read_lines:"Чтение фрагмента", file_metadata:"Проверка файла", list_workspace_files:"Просмотр файлов", search_files:"Поиск в файлах", edit_file:"Редактирование файла", copy_file:"Копирование файла", move_file:"Перемещение файла",
  get_time:"Проверка времени", create_task:"Создание задачи", plan_task:"Планирование задачи", start_next_task:"Начало задачи", execute_next_step:"Выполнение шага", update_task:"Обновление задачи", add_task_step:"Добавление шага", update_task_step:"Обновление шага",
  tool_search:"Выбор инструмента", propose_extension:"Подготовка инструмента", install_extension:"Установка инструмента", approval_decision:"Решение по действию", run_shell:"Выполнение команды", desktop_snapshot:"Просмотр экрана", desktop_interact:"Работа с приложением",
  create_automation:"Создание автоматизации", remember:"Сохранение в памяти", search_memory:"Обращение к памяти", create_long_task_plan:"Подготовка плана",
};
const statuses: Record<string,string> = { completed:"Готово",success:"Готово",succeeded:"Готово",delivered:"Доставлено",failed:"Не удалось выполнить",error:"Ошибка",running:"Выполняется",pending:"Ожидает выполнения",queued:"В очереди",blocked:"Действие приостановлено",approval_required:"Нужно подтверждение",waiting_approval:"Нужно подтверждение",validation_error:"Нужны другие параметры",invalid_input:"Нужны другие параметры",not_found:"Инструмент не найден",timeout:"Время ожидания истекло",cancelled:"Отменено",canceled:"Отменено",interrupted:"Прервано",partial:"Частичный результат",denied:"Не разрешено",approved:"Разрешено",cached:"Из кеша",retrying:"Повторная попытка",superseded:"Заменено" };
const completedNames: Record<string,string> = { create_docx:"Создал Word-файл",append_docx:"Дополнил документ",research_read:"Прочитал источник",web_search:"Нашёл источники",browser_read:"Открыл страницу",http_request:"Получил ответ сайта",analyze_image:"Проанализировал изображение",inspect_image:"Проверил изображение",transform_image:"Обработал изображение",read_file:"Прочитал файл",write_file:"Сохранил файл",inspect_office_document:"Прочитал документ",edit_office_document:"Обновил документ",create_presentation:"Создал презентацию",tool_search:"Проверил инструменты",get_time:"Проверил время" };
export function actionLabel(tool: string,status?: string): string { return status && ["completed","success","succeeded","cached"].includes(status) && completedNames[tool] ? completedNames[tool] : names[tool] || (tool.startsWith("connector_") ? "Работа с подключением" : "Действие Avelin"); }
export function statusLabel(status: string): string { return statuses[status] || "Статус недоступен"; }
function tone(status: string): string { return ["completed","success","succeeded","delivered","approved","cached"].includes(status) ? "success" : ["failed","error","validation_error","invalid_input","not_found","timeout","denied"].includes(status) ? "danger" : "neutral"; }
function icon(tool: string): IconName { return /image|desktop/.test(tool) ? "files" : /search|http|browser|research/.test(tool) ? "search" : /file|docx|document|presentation/.test(tool) ? "files" : /time|automation/.test(tool) ? "clock" : "tasks"; }
function excerpt(value: string): string { return value.length>12000 ? value.slice(0,12000)+"\n\n[Показан фрагмент записи]" : value; }

export function CompactActionLog({ logs, limit, compact=false, timezone }: { logs: ActionLogItem[]; limit?: number; compact?: boolean; timezone?: string }) {
  const visible=typeof limit === "number" ? logs.slice(0,limit) : logs;
  return <div className={`compact-action-log${compact ? " compact-action-log-small" : ""}`}>
    {visible.map((log) => <article className="compact-action" key={log.id}>
      <div className="compact-action-row"><span className="compact-action-icon"><WorkspaceIcon name={icon(log.tool_name)} /></span><div><strong>{actionLabel(log.tool_name,log.status)}</strong><span className={`action-status action-status-${tone(log.status)}`}>{statusLabel(log.status)}</span></div><time dateTime={log.created_at}>{panelTime(log.created_at,timezone)}</time></div>
      {!compact ? <details className="action-technical"><summary>Подробности</summary><dl><div><dt>Инструмент</dt><dd><code>{log.tool_name}</code></dd></div><div><dt>Статус</dt><dd>{statusLabel(log.status)}</dd></div></dl>{Object.keys(log.arguments || {}).length ? <><h4>Параметры</h4><pre>{excerpt(JSON.stringify(log.arguments,null,2))}</pre></> : null}{log.result ? <><h4>Результат</h4><pre>{excerpt(log.result)}</pre></> : null}</details> : null}
    </article>)}
    {!visible.length ? <p className="panel-muted-empty">Здесь появятся выполненные действия.</p> : null}
  </div>;
}
