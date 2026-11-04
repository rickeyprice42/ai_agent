import { useEffect, useRef, type ReactNode } from "react";
import { WorkspaceIcon } from "./WorkspaceIcon";

export function WorkspaceDialog({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement | null>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} className="workspace-dialog" aria-label={title} onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <header><h2>{title}</h2><button className="icon-button" aria-label="Закрыть окно" onClick={onClose} type="button"><WorkspaceIcon name="close" /></button></header>
    {children}
  </dialog>;
}
