import { useState } from "react";
import { WorkspaceDialog } from "./WorkspaceDialog";

export function deviceTimezone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; }
  catch { return "UTC"; }
}

export function TimezonePicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState("");
  const device = deviceTimezone();
  let offset = "";
  try { offset = new Intl.DateTimeFormat("ru", { timeZone: value, timeZoneName: "shortOffset" }).formatToParts(new Date()).find((part) => part.type === "timeZoneName")?.value || ""; } catch { /* Validation runs before applying a changed value. */ }
  const supported = (Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") || ["UTC", "Europe/Moscow", "Asia/Irkutsk", "Asia/Vladivostok"];
  function apply() {
    try {
      const canonical = new Intl.DateTimeFormat("ru", { timeZone: draft.trim() }).resolvedOptions().timeZone;
      onChange(canonical); setError(""); setOpen(false);
    } catch { setError("Выбери существующий часовой пояс IANA, например Asia/Irkutsk или Europe/Moscow."); }
  }
  return <div className="timezone-field" onKeyDown={(event) => { if (open && event.key === "Enter") { event.preventDefault(); apply(); } }}><span>Часовой пояс</span><div className="timezone-value"><strong>{value}{offset ? ` · ${offset}` : ""}</strong><button className="text-button" type="button" onClick={() => { setDraft(value); setError(""); setOpen(true); }}>Изменить часовой пояс</button></div><small>{value === device ? "Определён по устройству" : "Выбран вручную"}</small><WorkspaceDialog open={open} title="Часовой пояс" onClose={() => setOpen(false)}><label className="timezone-edit">Пояс IANA<input aria-label="Часовой пояс IANA" autoComplete="off" list="avelin-timezones" value={draft} onChange={(event) => setDraft(event.target.value)} /></label><datalist id="avelin-timezones">{Array.from(new Set(["UTC", device, ...supported])).map((zone) => <option value={zone} key={zone} />)}</datalist>{error ? <p role="alert" className="message-error">{error}</p> : null}<button className="text-button" type="button" onClick={() => { setDraft(device); setError(""); }}>Использовать пояс устройства</button><div className="dialog-button-row"><button className="ghost-button" type="button" onClick={() => setOpen(false)}>Отмена</button><button type="button" onClick={apply}>Применить</button></div></WorkspaceDialog></div>;
}
