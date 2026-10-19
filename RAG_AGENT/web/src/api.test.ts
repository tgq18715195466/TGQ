import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from './api';

describe('authenticated API lifecycle', () => {
  it('discards a response after logout/disposal', async () => {
    let resolve!: (r: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(r => { resolve = r; })));
    const api = new ApiClient('test-token', vi.fn());
    const pending = api.request('/me');
    api.dispose();
    resolve(new Response(JSON.stringify({ name: 'old account' })));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('includes authentication and idempotency for mutations', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"status":"CANCELLED"}'));
    vi.stubGlobal('fetch', fetcher);
    const api = new ApiClient('local-token', vi.fn());
    await api.mutate('/jobs/abc/cancel');
    const [path, opts] = fetcher.mock.calls[0];
    expect(path).toBe('/api/v1/jobs/abc/cancel');
    expect(new Headers(opts.headers).get('Authorization')).toBe('Bearer local-token');
    expect(new Headers(opts.headers).get('Idempotency-Key')).toBeTruthy();
  });

  it('expires login on 401 without returning server data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"message":"expired"}', { status: 401 })));
    const expired = vi.fn();
    await expect(new ApiClient('bad', expired).request('/me')).rejects.toThrow('expired');
    expect(expired).toHaveBeenCalledOnce();
  });
});
