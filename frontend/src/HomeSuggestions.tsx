import { useEffect, useState } from "react";
import { WorkspaceIcon } from "./WorkspaceIcon";
import { panelJson } from "./panelApi";
import "./component-panels.css";

export type HomeSuggestion = { id: string; title: string; prompt: string; source?: "memory" | "project"; source_id?: string };
export type HomeSuggestionPayload = { basic: HomeSuggestion[]; advanced: HomeSuggestion[]; personal: HomeSuggestion[]; personalization_enabled: boolean };
type Level = "basic" | "advanced" | "personal";

export function HomeSuggestions({ token, onSelect, disabled=false }: { token: string; onSelect: (prompt: string) => void; disabled?: boolean }) {
  const [data,setData]=useState<HomeSuggestionPayload|null>(null);
  const [owner,setOwner]=useState("");
  const [level,setLevel]=useState<Level>("basic");
  const [rotation,setRotation]=useState(0);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  const [retry,setRetry]=useState(0);
  useEffect(() => {
    const abort=new AbortController();
    setData(null);setOwner("");setLevel("basic");setRotation(0);setError("");setLoading(true);
    void panelJson<HomeSuggestionPayload>("/api/home/suggestions",token,abort.signal).then((value)=> {
      if (!abort.signal.aborted) { setData(value);setOwner(token); }
    }).catch((reason)=> { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить идеи."); })
      .finally(()=> { if (!abort.signal.aborted) setLoading(false); });
    return ()=>abort.abort();
  },[token,retry]);
  const current=owner===token ? data : null;
  const personal=Boolean(current?.personalization_enabled && current.personal.length);
  const active=level==="personal" && !personal ? "basic" : level;
  const choices=current?.[active] || [];
  const displayed=choices.length ? [...choices.slice(rotation%choices.length),...choices.slice(0,rotation%choices.length)].slice(0,3) : [];
  const levels: Array<[Level,string]>=[["basic","На каждый день"],["advanced","Большие задачи"],...(personal ? [["personal","Для тебя"] as [Level,string]] : [])];
  return <section className="home-idea-panel" aria-label="Идеи для Avelin" aria-busy={loading}>
    <div className="home-idea-toolbar"><div className="home-idea-tabs" aria-label="Категории идей">{levels.map(([id,label])=><button type="button" aria-pressed={active===id} key={id} onClick={()=>{setLevel(id);setRotation(0);}}>{label}</button>)}</div>{choices.length>3 ? <button className="text-button home-ideas-more" type="button" onClick={()=>setRotation((value)=>value+3)} disabled={disabled}>Другие идеи <span aria-hidden="true">↻</span></button> : null}</div>
    <div className="home-idea-grid">{displayed.map((idea)=><button className="home-idea" key={idea.id} type="button" disabled={disabled} onClick={()=>onSelect(idea.prompt)}><strong>{idea.title}</strong><span>{idea.prompt}</span><WorkspaceIcon name="arrow" /></button>)}</div>
    {loading ? <p className="panel-loading" role="status">Подбираю идеи…</p> : null}
    {error ? <div className="home-idea-error"><span>Не удалось загрузить идеи.</span><button className="text-button" title={error} type="button" onClick={()=>setRetry((value)=>value+1)}>Повторить</button></div> : null}
    {!loading && !error && !displayed.length ? <p className="panel-muted-empty">Начни с собственного запроса — Avelin поможет.</p> : null}
  </section>;
}
