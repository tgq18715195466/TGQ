import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { DocumentRecord } from './types';

export const terminal = new Set(['READY', 'COMPLETED', 'FAILED', 'CANCELLED', 'DELETING', 'DELETED']);
const statusNames: Record<string, string> = {
  READY: '已就绪', COMPLETED: '已完成', QUEUED: '等待处理', RUNNING: '处理中',
  RETRYING: '重试中', FAILED: '处理失败', CANCELLED: '已取消', PENDING: '待处理',
  PROCESSING: '处理中', DELETING: '删除中', DELETED: '已删除',
};

export const date = (value?: string | null) => value
  ? new Date(value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
  : '—';
export const statusOf = (doc: DocumentRecord) => doc.job?.status || doc.latest_version?.status || doc.status || 'PENDING';

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> {
  children: ReactNode;
  onClick?: () => void | Promise<void>;
  className?: string;
}
export function Button({ children, onClick, className = '', type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={className} onClick={onClick} {...props}>{children}</button>;
}

export function PageHead({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return <div className="page-head"><div><h1>{title}</h1>{description && <p>{description}</p>}</div><div className="actions">{children}</div></div>;
}
export function Badge({ status }: { status: string }) {
  const tone = ['READY', 'COMPLETED'].includes(status) ? 'good' : ['FAILED', 'CANCELLED'].includes(status) ? 'bad' : 'busy';
  return <span className={`badge ${tone}`}>{statusNames[status] || status}</span>;
}
export function Empty({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-symbol">▤</div><strong>{title}</strong>{description && <p>{description}</p>}{children}</div>;
}
export function Modal({ title, onClose, children, drawer = false }: {
  title: string; onClose: () => void; children: ReactNode; drawer?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    element.showModal();
    const close = () => onCloseRef.current();
    element.addEventListener('cancel', close);
    return () => { element.removeEventListener('cancel', close); if (element.open) element.close(); };
  }, []);
  return createPortal(<dialog ref={dialog} className={drawer ? 'drawer' : ''} onClick={event => {
    if (event.target === dialog.current) onClose();
  }}>
    <div className="dialog-head"><div>{drawer && <span className="eyebrow">KNOWLEDGE EXPLORER</span>}<h2>{title}</h2></div>
      <button className="icon-btn" type="button" aria-label={drawer ? '关闭详情' : '关闭对话框'} onClick={onClose}>×</button></div>
    <div className={drawer ? 'drawer-body' : 'modal-body'}>{children}</div>
  </dialog>, document.body);
}
