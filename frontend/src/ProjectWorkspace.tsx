import { useEffect, useState, type FormEvent } from "react";
import type { ChatThreadItem, ProjectItem } from "./types";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { WorkspaceIcon } from "./WorkspaceIcon";

export type ProjectDialogMode = "create" | "settings" | null;
type Props = {
  projects: ProjectItem[]; archived: ProjectItem[]; project?: ProjectItem | null;
  threads: ChatThreadItem[]; busy: boolean; dialog: ProjectDialogMode; error?: string | null;
  onDialog: (mode: ProjectDialogMode) => void; onBack: () => void;
  onOpen: (id: string) => void; onChat: (id: string) => void; onNewChat: () => void;
  onSave: (title: string, description: string) => Promise<void>;
  onArchive: () => void; onDelete: () => void; onRestore: (id: string) => void;
};

export function ProjectWorkspace(props: Props) {
  const { project, projects, archived, threads, busy, dialog } = props;
  const [search, setSearch] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  useEffect(() => { setSearch(""); }, [project?.id]);
  useEffect(() => {
    if (dialog) { setTitle(dialog === "create" ? "" : project?.title || ""); setDescription(dialog === "create" ? "" : project?.description || ""); }
  }, [dialog, project?.id]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!busy && title.trim()) await props.onSave(title.trim(), description.trim());
  }
  const visible = threads.filter((thread) => thread.title.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const date = (value: string) => { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "" : new Intl.DateTimeFormat("ru", { day: "numeric", month: "short" }).format(parsed); };
  return <section className={project ? "project-workspace" : "projects-index"}>
    {project ? <>
      <nav className="project-breadcrumb" aria-label="Путь проекта"><button type="button" onClick={props.onBack}>Проекты</button><span aria-hidden="true">/</span><span aria-current="page">{project.title}</span></nav>
      <header className="project-detail-header"><div><span className="project-symbol"><WorkspaceIcon name="projects" /></span><h2>{project.title}</h2>{project.description ? <p>{project.description}</p> : null}<small>{threads.length} диалогов · Личный проект</small></div><button className="icon-button" type="button" aria-label="Настройки проекта" disabled={busy} onClick={() => props.onDialog("settings")}><WorkspaceIcon name="settings" /></button></header>
      <div className="project-chat-toolbar"><label className="project-chat-search"><WorkspaceIcon name="search" /><input aria-label="Поиск диалогов проекта" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Найти диалог" /></label><button className="project-new-chat" type="button" disabled={busy || project.role === "viewer"} onClick={props.onNewChat}><WorkspaceIcon name="plus" />Новый диалог</button></div>
      <div className="project-thread-list">{visible.map((thread) => <button className="project-thread" type="button" key={thread.id} disabled={busy} onClick={() => props.onChat(thread.id)}><span className="card-icon"><WorkspaceIcon name="chat" /></span><span><strong>{thread.title}</strong><small>{thread.message_count ? `${thread.message_count} сообщений` : "Черновик"}</small></span><time>{date(thread.updated_at)}</time><WorkspaceIcon name="arrow" /></button>)}</div>
      {visible.length === 0 ? <div className="quiet-empty"><WorkspaceIcon name={search ? "search" : "chat"} /><p>{search ? "Диалоги не найдены" : "Первый диалог в этом проекте"}</p><span>{search ? "Попробуй другое название." : "Начни отдельную задачу — её история останется здесь."}</span>{!search ? <button className="text-button" type="button" disabled={busy || project.role === "viewer"} onClick={props.onNewChat}>Начать диалог<WorkspaceIcon name="arrow" /></button> : null}</div> : null}
    </> : <>
      <div className="projects-index-toolbar"><p className="muted">Отдельные пространства для твоих задач и диалогов.</p><button className="ghost-button" type="button" disabled={busy} onClick={() => props.onDialog("create")}><WorkspaceIcon name="plus" />Новый проект</button></div>
      <div className="project-grid">{projects.map((item) => <button type="button" className="project-card" key={item.id} disabled={busy} onClick={() => props.onOpen(item.id)}><span className="project-symbol"><WorkspaceIcon name="projects" /></span><strong>{item.title}</strong>{item.description ? <p>{item.description}</p> : null}<footer><small>{item.chat_count} диалогов</small><WorkspaceIcon name="arrow" /></footer></button>)}</div>
      {!projects.length ? <div className="quiet-empty"><WorkspaceIcon name="projects" /><p>Здесь появятся твои проекты</p><span>Создай пространство для работы, учёбы или личной идеи.</span></div> : null}
      {archived.length ? <details className="project-archive"><summary>Архив проектов ({archived.length})</summary>{archived.map((item) => <div key={item.id}><span>{item.title}</span><button className="text-button" type="button" disabled={busy} onClick={() => props.onRestore(item.id)}>Восстановить</button></div>)}</details> : null}
    </>}
    <WorkspaceDialog open={Boolean(dialog)} title={dialog === "create" ? "Новый проект" : "Настройки проекта"} onClose={() => { if (!busy) props.onDialog(null); }}>{props.error ? <p className="message-error" role="alert">{props.error}</p> : null}<form className="dialog-form" onSubmit={(event) => void save(event)}><label>Название проекта<input aria-label="Название проекта" value={title} required maxLength={120} onChange={(event) => setTitle(event.target.value)} disabled={busy || (dialog === "settings" && project?.role === "viewer")} /></label><label>Описание<textarea aria-label="Описание проекта" value={description} maxLength={1000} rows={3} onChange={(event) => setDescription(event.target.value)} disabled={busy || (dialog === "settings" && project?.role === "viewer")} /></label><button type="submit" disabled={busy || !title.trim() || (dialog === "settings" && project?.role === "viewer")}>{busy ? "Сохраняем…" : dialog === "create" ? "Создать проект" : "Сохранить изменения"}</button></form>{dialog === "settings" && project?.role === "owner" ? <details className="project-settings-actions"><summary>Управление проектом</summary><button className="text-button" type="button" disabled={busy} onClick={props.onArchive}>В архив</button><button className="text-button" type="button" disabled={busy} onClick={props.onDelete}>Удалить проект</button><small>История диалогов сохраняется.</small></details> : null}</WorkspaceDialog>
  </section>;
}
