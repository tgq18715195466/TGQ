import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DocumentRecord } from '../types';
import Documents from './Documents';

const workspace = vi.hoisted(() => ({
  api: { request: vi.fn(), mutate: vi.fn(), download: vi.fn() },
  kbs: [{ id: 'kb-1', name: '团队资料', created_at: '2026-09-24' }],
  docs: [{ id: 'doc-1', kb_id: 'kb-1', name: '操作手册.pdf', created_at: '2026-09-24', status: 'READY', current_version_id: 'v1', latest_version: null, job: null }] as DocumentRecord[],
  selectedKB: 'kb-1', setSelectedKB: vi.fn(), refresh: vi.fn(), notify: vi.fn(), onError: vi.fn(), openNewKB: vi.fn(), openPreview: vi.fn(),
}));
vi.mock('../workspace', () => ({ useWorkspace: () => workspace }));

describe('Documents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workspace.docs[0].status = 'READY';
    workspace.docs[0].job = null;
    workspace.api.request.mockResolvedValue({});
    workspace.api.mutate.mockResolvedValue({});
    workspace.refresh.mockResolvedValue(undefined);
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    HTMLDialogElement.prototype.close = function () { this.open = false; };
  });
  it('preserves the focused filter when refreshed data renders', () => {
    const { rerender } = render(<Documents />);
    const search = screen.getByRole('textbox', { name: '搜索文档' });
    search.focus();
    fireEvent.change(search, { target: { value: '不存在' } });
    expect(screen.queryByRole('button', { name: '操作手册.pdf' })).not.toBeInTheDocument();
    rerender(<Documents />);
    expect(search).toHaveFocus();
    expect(search).toHaveValue('不存在');
    fireEvent.change(search, { target: { value: '操作' } });
    expect(screen.getByRole('button', { name: '操作手册.pdf' })).toBeInTheDocument();
  });
  it('uploads a dropped file with canonical MIME and an idempotency key', async () => {
    render(<Documents />);
    const file = new File(['hello'], 'notes.md', { type: 'application/octet-stream' });
    fireEvent.drop(screen.getByRole('button', { name: '选择文件上传' }), { dataTransfer: { files: [file] } });
    await waitFor(() => expect(workspace.api.request).toHaveBeenCalledTimes(1));
    const [path, options] = workspace.api.request.mock.calls[0];
    expect(path).toBe('/kbs/kb-1/documents');
    expect(options.headers['Idempotency-Key']).toBeTruthy();
    expect(options.body.get('file').type).toBe('text/markdown');
    await waitFor(() => expect(workspace.refresh).toHaveBeenCalled());
  });
  it('rejects unsupported dropped files before sending a request', async () => {
    render(<Documents />);
    fireEvent.drop(screen.getByRole('button', { name: '选择文件上传' }), { dataTransfer: { files: [new File(['x'], 'script.exe')] } });
    await waitFor(() => expect(workspace.onError).toHaveBeenCalled());
    expect(workspace.api.request).not.toHaveBeenCalled();
  });
  it('cancels a running job and refreshes its status', async () => {
    workspace.docs[0].job = { id: 'job-1', status: 'RUNNING', current_stage: 'chunk_document', progress: 0.5 };
    render(<Documents />);
    expect(screen.getByRole('progressbar', { name: '文档处理进度' })).toHaveValue(0.5);
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(workspace.api.mutate).toHaveBeenCalledWith('/jobs/job-1/cancel'));
    await waitFor(() => expect(workspace.refresh).toHaveBeenCalled());
  });
  it('requires confirmation before deleting a document', async () => {
    render(<Documents />);
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    expect(workspace.api.mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '删除文档' }));
    await waitFor(() => expect(workspace.api.mutate).toHaveBeenCalledWith('/documents/doc-1', {}, 'DELETE'));
  });
});
