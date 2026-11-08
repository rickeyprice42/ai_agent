import { useState } from "react";
import type { ProjectItem } from "./types";
import { WorkspaceIcon } from "./WorkspaceIcon";
import "./component-panels.css";

function chatCount(value: number): string { const mod=value%100,last=value%10;return `${value} ${mod>=11 && mod<=14 ? "диалогов" : last===1 ? "диалог" : last>=2 && last<=4 ? "диалога" : "диалогов"}`; }

export function SharedWorkspace({ groups, busy=false, onOpen, onCreate, onJoin }: { groups: ProjectItem[]; busy?: boolean; onOpen: (id:string)=>void; onCreate:()=>void; onJoin:()=>void }) {
  const [search,setSearch]=useState("");
  const visible=groups.filter((group)=>`${group.title} ${group.description}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <section className="shared-messenger-index">
    <header><div><h3>Общие диалоги</h3><p>Для разговоров и работы вместе.</p></div><button className="primary-button" type="button" disabled={busy} onClick={onCreate}><WorkspaceIcon name="plus" />Новая группа</button></header>
    {groups.length ? <label className="shared-messenger-search"><WorkspaceIcon name="search" /><input aria-label="Найти группу" placeholder="Найти группу" value={search} onChange={(event)=>setSearch(event.target.value)} /></label> : null}
    <div className="shared-messenger-list">{visible.map((group)=><button className="shared-messenger-group" type="button" key={group.id} disabled={busy} onClick={()=>onOpen(group.id)}><span className="shared-messenger-avatar" aria-hidden="true">{group.title.slice(0,1).toLocaleUpperCase()}</span><span><strong>{group.title}</strong><small>{group.description || "Общий чат с участниками и Avelin"}</small></span><span className="shared-messenger-meta"><small>{chatCount(group.chat_count)}</small><WorkspaceIcon name="arrow" /></span></button>)}</div>
    {!visible.length ? <div className="panel-quiet-empty shared-messenger-empty"><WorkspaceIcon name={search ? "search" : "chat"} /><h3>{search ? "Группа не найдена" : "Вместе удобнее"}</h3><p>{search ? "Попробуй другое название." : "Создай группу и пригласи участников по ссылке."}</p></div> : null}
    <footer><span>Есть приглашение?</span><button type="button" className="text-button" disabled={busy} onClick={onJoin}>Присоединиться к группе <WorkspaceIcon name="arrow" /></button></footer>
  </section>;
}

export function SharedChatHeader({ project,participantCount,onBack,onSettings }: { project:ProjectItem;participantCount?:number;onBack:()=>void;onSettings:()=>void }) {
  const agent=project.agent_mode==="off" ? "Avelin выключен" : project.agent_mode==="explicit" ? "Avelin участвует" : "Avelin по обращению";
  return <div className="shared-messenger-header"><button className="icon-button" type="button" aria-label="Все группы" title="Все группы" onClick={onBack}><span aria-hidden="true">←</span></button><span className="shared-messenger-avatar" aria-hidden="true">{project.title.slice(0,1).toLocaleUpperCase()}</span><div><strong title={project.title}>{project.title}</strong><span>{typeof participantCount==="number" && participantCount>0 ? `${participantCount} в группе · ` : ""}{project.role==="viewer" ? "Только чтение" : agent}</span></div><button className="icon-button" type="button" aria-label="Настройки группы" title="Настройки группы" onClick={onSettings}><WorkspaceIcon name="settings" /></button></div>;
}
