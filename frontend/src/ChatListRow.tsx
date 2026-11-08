import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ChatThreadItem } from "./types";

export function ChatListRow({ thread, selected, disabled, canDelete, onOpen, onDelete }: { thread: ChatThreadItem; selected: boolean; disabled: boolean; canDelete: boolean; onOpen: () => void; onDelete: () => void }) {
  const [menu, setMenu] = useState<{ left: number; top: number } | null>(null);
  const row = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const hold = useRef<number | null>(null);
  const start = useRef({ x: 0, y: 0 });
  const suppressClick = useRef(false);
  function cancelHold() { if (hold.current !== null) window.clearTimeout(hold.current); hold.current = null; }
  function openMenu(target: HTMLElement) {
    if (disabled || !canDelete || !row.current) return;
    cancelHold(); trigger.current = target;
    const box = row.current.getBoundingClientRect();
    setMenu({ left: Math.max(12, Math.min(box.right - 200, innerWidth - 212)), top: Math.max(12, Math.min(box.bottom + 4, innerHeight - 80)) });
  }
  function close() { setMenu(null); if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true }); }
  useEffect(() => () => cancelHold(), []);
  useEffect(() => {
    if (!menu) return;
    popup.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    function outside(event: Event) { if (!popup.current?.contains(event.target as Node) && !row.current?.contains(event.target as Node)) close(); }
    function key(event: globalThis.KeyboardEvent) { if (event.key === "Escape" || event.key === "Tab") { if (event.key === "Escape") event.preventDefault(); close(); } }
    function scrolled(event: Event) { if (event.target === document || event.target === window || (event.target instanceof Element && row.current && event.target.contains(row.current))) close(); }
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", key);
    window.addEventListener("resize", close); window.addEventListener("scroll", scrolled, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", key); window.removeEventListener("resize", close); window.removeEventListener("scroll", scrolled, true); };
  }, [menu]);
  return <div className="chat-list-row" ref={row}>
    <button type="button" disabled={disabled} aria-current={selected ? "true" : undefined} className={`chat-list-item${selected ? " is-active" : ""}`} title={thread.title}
      onClick={(event) => { if (suppressClick.current) { suppressClick.current = false; event.preventDefault(); return; } onOpen(); }}
      onContextMenu={(event) => { if (canDelete) { event.preventDefault(); openMenu(event.currentTarget); } }}
      onKeyDown={(event) => { if (canDelete && (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10"))) { event.preventDefault(); openMenu(event.currentTarget); } }}
      onPointerDown={(event) => { suppressClick.current = false; if (event.pointerType === "mouse" || !canDelete || disabled) return; start.current = { x: event.clientX, y: event.clientY }; const target = event.currentTarget; hold.current = window.setTimeout(() => { suppressClick.current = true; openMenu(target); }, 550); }}
      onPointerMove={(event) => { if (Math.hypot(event.clientX - start.current.x, event.clientY - start.current.y) > 10) cancelHold(); }}
      onPointerUp={cancelHold} onPointerCancel={cancelHold} onPointerLeave={cancelHold}>
      <span>{thread.title}</span><small>{thread.message_count} сообщений</small>
    </button>
    {canDelete ? <button className="chat-row-menu-toggle icon-button" type="button" aria-label={`Действия: ${thread.title}`} aria-haspopup="menu" aria-expanded={Boolean(menu)} disabled={disabled} onClick={(event) => menu ? close() : openMenu(event.currentTarget)}>•••</button> : null}
    {menu ? createPortal(<div ref={popup} role="menu" aria-label={`Действия с диалогом ${thread.title}`} className="chat-row-menu" style={menu}><button role="menuitem" type="button" onClick={() => { close(); onDelete(); }}>Удалить диалог</button></div>, document.body) : null}
  </div>;
}
