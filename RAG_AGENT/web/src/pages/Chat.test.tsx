import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Chat from './Chat';

const workspace = vi.hoisted(() => ({
  api: { request: vi.fn(), mutate: vi.fn(), stream: vi.fn() },
  me: { id: 'user-1', tenant_id: 'tenant-1', name: '小李' },
  kbs: [{ id: 'kb-1', name: '团队资料', created_at: '2026-09-24' }],
  sessions: [
    { id: 'session-1', title: '第一个会话', created_at: '2026-09-24' },
    { id: 'session-2', title: '第二个会话', created_at: '2026-09-24' },
  ],
  status: { external_services_verified: true },
  selectedKB: 'kb-1', setSelectedKB: vi.fn(), refresh: vi.fn(), notify: vi.fn(), onError: vi.fn(), openPreview: vi.fn(),
}));
vi.mock('../workspace', () => ({ useWorkspace: () => workspace }));

describe('Chat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workspace.api.request.mockResolvedValue({ messages: [], runs: [], citations: [] });
    workspace.api.mutate.mockResolvedValue({});
    workspace.refresh.mockResolvedValue(undefined);
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    HTMLDialogElement.prototype.close = function () { this.open = false; };
  });

  it('ignores a late history response after changing sessions', async () => {
    let resolveFirst!: (value: unknown) => void;
    workspace.api.request.mockImplementation((path: string) => {
      if (path.includes('session-1')) return new Promise(resolve => { resolveFirst = resolve; });
      return Promise.resolve({ messages: [{ role: 'user', content: '第二个问题', run_id: 'run-2' }], runs: [], citations: [] });
    });
    render(<Chat />);
    fireEvent.click(screen.getByRole('button', { name: '第一个会话' }));
    fireEvent.click(screen.getByRole('button', { name: '第二个会话' }));
    await screen.findByText('第二个问题');
    resolveFirst({ messages: [{ role: 'user', content: '过期内容', run_id: 'run-1' }], runs: [], citations: [] });
    await waitFor(() => expect(screen.queryByText('过期内容')).not.toBeInTheDocument());
  });

  it('submits a question once and carries its knowledge base into the run', async () => {
    workspace.api.request.mockImplementation((path: string) => {
      if (path === '/chat/sessions') return Promise.resolve({ id: 'session-3', title: '新问题', created_at: '2026-09-24' });
      if (path === '/chat/runs') return Promise.resolve({ run_id: 'run-3' });
      return Promise.resolve({ messages: [], runs: [], citations: [] });
    });
    workspace.api.stream.mockImplementation(() => new Promise(() => {}));
    render(<Chat />);
    fireEvent.change(screen.getByRole('textbox', { name: '输入问题' }), { target: { value: '如何操作？' } });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));
    await waitFor(() => expect(workspace.api.request).toHaveBeenCalledWith('/chat/runs', expect.objectContaining({
      body: JSON.stringify({ session_id: 'session-3', question: '如何操作？', kb_ids: ['kb-1'], web_search_enabled: false }),
    })));
    expect(workspace.api.stream).toHaveBeenCalledWith('/chat/runs/run-3/events', '0', expect.any(AbortSignal));
    expect(screen.getByText('如何操作？')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument();
  });

  it('releases the active stream when opening a new session', async () => {
    workspace.api.request.mockImplementation((path: string) => {
      if (path === '/chat/sessions') return Promise.resolve({ id: 'session-3', title: '新问题', created_at: '2026-09-24' });
      if (path === '/chat/runs') return Promise.resolve({ run_id: 'run-3' });
      return Promise.resolve({ messages: [], runs: [], citations: [] });
    });
    workspace.api.stream.mockImplementation(() => new Promise(() => {}));
    render(<Chat />);
    fireEvent.change(screen.getByRole('textbox', { name: '输入问题' }), { target: { value: '如何操作？' } });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));
    await screen.findByRole('button', { name: '停止生成' });
    fireEvent.click(screen.getByRole('button', { name: /开启新会话/ }));
    expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /发送/ })).toBeEnabled();
  });
});
