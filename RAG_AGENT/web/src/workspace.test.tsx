import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { WorkspaceProvider, useWorkspace } from './workspace';

function AgentEntry() {
  const { me } = useWorkspace();
  return <div>Agent 已为 {me.name} 开启</div>;
}

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/');
  vi.restoreAllMocks();
});

it('requires username and password before rendering the Agent workspace', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === '/api/v1/auth/login') {
      expect(init?.method).toBe('POST');
      expect(JSON.parse(String(init?.body))).toEqual({ username: 'admin', password: 'test-password-123' });
      return new Response(JSON.stringify({ access_token: 'issued-token', token_type: 'bearer', expires_in: 3600 }), { status: 200 });
    }
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer issued-token');
    const data = path.endsWith('/me') ? { id: 'user-1', name: '管理员', tenant_id: 'tenant-1' }
      : path.endsWith('/system/status') ? { api: 'ok', external_services_verified: false } : [];
    return new Response(JSON.stringify(data), { status: 200 });
  });
  render(<WorkspaceProvider><AgentEntry /></WorkspaceProvider>);
  expect(screen.queryByText(/Agent 已为/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '用户名' }), { target: { value: 'admin' } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password-123' } });
  fireEvent.click(screen.getByRole('button', { name: /进入工作空间/ }));
  expect(await screen.findByText('Agent 已为 管理员 开启')).toBeInTheDocument();
  expect(sessionStorage.getItem('zg.session.v2')).toBe('issued-token');
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/login', expect.anything());
});

it('does not accept a legacy URL token as a login', () => {
  window.history.replaceState(null, '', '/#token=legacy-token');
  render(<WorkspaceProvider><AgentEntry /></WorkspaceProvider>);
  expect(screen.getByRole('textbox', { name: '用户名' })).toBeInTheDocument();
  expect(screen.queryByText(/Agent 已为/)).not.toBeInTheDocument();
  expect(sessionStorage.getItem('zg.session.v2')).toBeNull();
});

it('keeps the login screen after an incorrect password', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ message: '请提供有效凭证。' }), { status: 401 }));
  render(<WorkspaceProvider><AgentEntry /></WorkspaceProvider>);
  fireEvent.change(screen.getByRole('textbox', { name: '用户名' }), { target: { value: 'admin' } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'wrong-password' } });
  fireEvent.click(screen.getByRole('button', { name: /进入工作空间/ }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('请提供有效凭证。'));
  expect(screen.queryByText(/Agent 已为/)).not.toBeInTheDocument();
});
