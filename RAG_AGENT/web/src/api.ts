export class ApiClient {
  private disposed = false;
  private readonly controllers = new Set<AbortController>();

  constructor(private readonly token: string, private readonly onUnauthorized: () => void) {}

  dispose(): void {
    this.disposed = true;
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  }

  private async fetch(path: string, init: RequestInit = {}): Promise<Response> {
    if (this.disposed) throw new DOMException('Session closed', 'AbortError');
    const controller = new AbortController();
    this.controllers.add(controller);
    const externalSignal = init.signal;
    if (externalSignal?.aborted) controller.abort();
    externalSignal?.addEventListener('abort', () => controller.abort(), { once: true });
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${this.token}`);
    try {
      const response = await fetch(`/api/v1${path}`, { ...init, headers, signal: controller.signal });
      if (this.disposed) throw new DOMException('Session closed', 'AbortError');
      if (response.status === 401) this.onUnauthorized();
      if (!response.ok) {
        let message = `请求失败 (${response.status})`;
        try {
          const body = await response.json() as { message?: string; error_code?: string };
          message = `${body.message || message}${body.error_code ? ` (${body.error_code})` : ''}`;
        } catch { /* Response may not be JSON. */ }
        throw new Error(message);
      }
      return response;
    } finally {
      this.controllers.delete(controller);
    }
  }

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await this.fetch(path, options);
    if (this.disposed) throw new DOMException('Session closed', 'AbortError');
    if (response.status === 204) return null as T;
    const result = await response.json() as T;
    if (this.disposed) throw new DOMException('Session closed', 'AbortError');
    return result;
  }

  async mutate<T>(path: string, body: unknown = {}, method = 'POST'): Promise<T> {
    return this.request<T>(path, {
      method,
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(body),
    });
  }

  stream(path: string, lastId: string, signal?: AbortSignal): Promise<Response> {
    return this.fetch(path, { headers: { 'Last-Event-ID': lastId }, signal });
  }

  async download(path: string): Promise<Blob> {
    const response = await this.fetch(path);
    const blob = await response.blob();
    if (this.disposed) throw new DOMException('Session closed', 'AbortError');
    return blob;
  }
}
