import { createContext, useCallback, useContext, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { ApiClient } from './api';
import { Empty, Modal } from './components';
import type { ChatSession, Chunk, DocumentRecord, KnowledgeBase, SystemStatus, User } from './types';

export interface WorkspaceContextValue {
  api: ApiClient;
  me: User;
  kbs: KnowledgeBase[];
  docs: DocumentRecord[];
  sessions: ChatSession[];
  status: SystemStatus;
  selectedKB: string;
  setSelectedKB: (id: string) => void;
  refresh: () => Promise<void>;
  notify: (text: string) => void;
  onError: (error: unknown) => void;
  logout: () => void;
  openNewKB: () => void;
  openPreview: (doc: Pick<DocumentRecord, 'id' | 'name'>) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('Workspace context is unavailable');
  return context;
}

function initialToken(): string {
  if (window.location.hash.startsWith('#token=')) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  sessionStorage.removeItem('zg.token');
  return sessionStorage.getItem('zg.session.v2') || '';
}

async function pagedDocuments(api: ApiClient, kbs: KnowledgeBase[]): Promise<DocumentRecord[]> {
  const lists = await Promise.all(kbs.map(async kb => {
    const result: DocumentRecord[] = [];
    for (let offset = 0; ; offset += 100) {
      const page = await api.request<DocumentRecord[]>(`/kbs/${kb.id}/documents?offset=${offset}&limit=100`);
      result.push(...page);
      if (page.length < 100) return result;
    }
  }));
  return lists.flat();
}

function Login({ onLogin, error, busy }: { onLogin: (username: string, password: string) => Promise<void>; error: string; busy: boolean }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const submit = (event: FormEvent) => { event.preventDefault(); void onLogin(username.trim(), password).finally(() => setPassword('')); };
  return <div className="login-shell">
    <section className="login-brand"><div className="brand"><span className="brand-mark">掌</span><div>掌柜智库<small>KNOWLEDGE WORKSPACE</small></div></div>
      <div><span className="eyebrow">让知识，成为生产力</span><h1>每一次提问，<br />都有据可循。</h1><p>连接企业知识，发现可信答案。<br />从一份文档开始，构建团队共同的智慧。</p></div>
      <span className="login-foot">企业知识管理 · 可追溯智能问答</span></section>
    <section className="login-form"><form onSubmit={submit}><span className="eyebrow">欢迎回来</span><h2>连接你的工作空间</h2>
      <p className="muted">使用用户名和密码登录，开启你的知识工作空间。</p>
      <label htmlFor="username">用户名</label>
      <input id="username" type="text" required autoComplete="username" maxLength={64} placeholder="输入用户名" value={username} onChange={event => setUsername(event.target.value)} />
      <label htmlFor="password">密码</label>
      <input id="password" type="password" required autoComplete="current-password" placeholder="输入密码" value={password} onChange={event => setPassword(event.target.value)} />
      <p className="hint">登录状态仅保存在当前浏览器会话中。</p><button className="primary wide" type="submit" disabled={busy}>进入工作空间 <span>→</span></button>
      <p role="alert" className="error">{error}</p></form></section>
  </div>;
}

function NewKnowledgeBase({ api, onClose, onCreated, onError }: {
  api: ApiClient; onClose: () => void; onCreated: (id: string) => Promise<void>; onError: (error: unknown) => void;
}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const kb = await api.request<KnowledgeBase>('/knowledge-bases', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }),
      });
      await onCreated(kb.id);
    } catch (error) { onError(error); setBusy(false); }
  };
  return <Modal title="新建知识库" onClose={onClose}><form onSubmit={event => { void submit(event); }}>
    <label htmlFor="new-kb-name">知识库名称</label><input id="new-kb-name" className="wide" required maxLength={200}
      placeholder="例如：产品资料、团队制度、项目档案" value={name} onChange={event => setName(event.target.value)} />
    <p className="field-help">为相同主题的文档建立一个共同的家。</p>
    <div className="dialog-actions"><button type="button" onClick={onClose}>取消</button><button className="primary" type="submit" disabled={busy}>创建知识库</button></div>
  </form></Modal>;
}

function DocumentPreview({ api, doc, onClose }: { api: ApiClient; doc: Pick<DocumentRecord, 'id' | 'name'>; onClose: () => void }) {
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let mounted = true;
    void api.request<Chunk[]>(`/documents/${doc.id}/content?kind=chunks&limit=100`).then(rows => {
      if (mounted) setChunks(rows);
    }).catch(reason => {
      if (mounted && reason?.name !== 'AbortError') setError(reason instanceof Error ? reason.message : '读取文档失败');
    });
    return () => { mounted = false; };
  }, [api, doc.id]);
  return <Modal title={doc.name} onClose={onClose} drawer>
    {error ? <p className="error">{error}</p> : !chunks ? <div className="loading">正在读取已发布文档内容…</div> : <>
      <p className="hint">以下内容来自当前已发布版本；展示前 100 个文本片段。</p>
      {chunks.length ? chunks.map((chunk, index) => <section className="chunk" key={chunk.id}>
        <div className="chunk-label">片段 {index + 1} · 第 {chunk.page_start}–{chunk.page_end} 页</div>
        <div className="raw-text">{chunk.chunk_text}</div>
      </section>) : <Empty title="文档内容尚未就绪" description="解析和索引完成后，已发布版本的文本片段会出现在这里。" />}
    </>}
  </Modal>;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(initialToken);
  const [loginError, setLoginError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);
  const [loading, setLoading] = useState(Boolean(token));
  const [me, setMe] = useState<User | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [selectedKB, setSelectedKB] = useState('');
  const [toast, setToast] = useState('');
  const [newKB, setNewKB] = useState(false);
  const [preview, setPreview] = useState<Pick<DocumentRecord, 'id' | 'name'> | null>(null);

  const logout = useCallback(() => {
    sessionStorage.removeItem('zg.session.v2');
    setToken(''); setMe(null); setKbs([]); setDocs([]); setSessions([]); setStatus(null);
    setSelectedKB(''); setNewKB(false); setPreview(null); setLoading(false);
  }, []);
  const api = useMemo(() => token ? new ApiClient(token, logout) : null, [token, logout]);
  useEffect(() => () => api?.dispose(), [api]);

  const refresh = useCallback(async () => {
    if (!api) return;
    const [profile, knowledgeBases, chats, system] = await Promise.all([
      api.request<User>('/me'), api.request<KnowledgeBase[]>('/knowledge-bases'),
      api.request<ChatSession[]>('/chat/sessions'), api.request<SystemStatus>('/system/status'),
    ]);
    const documents = await pagedDocuments(api, knowledgeBases);
    setMe(profile); setKbs(knowledgeBases); setDocs(documents); setSessions(chats); setStatus(system);
    setSelectedKB(previous => previous || knowledgeBases[0]?.id || '');
  }, [api]);
  useEffect(() => {
    if (!api) return;
    let mounted = true;
    setLoading(true);
    void refresh().catch(error => {
      if (!mounted || error?.name === 'AbortError') return;
      setLoginError(error instanceof Error ? error.message : '连接工作空间失败');
      logout();
    }).finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [api, refresh, logout]);

  const notify = useCallback((value: string) => setToast(value), []);
  const onError = useCallback((error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    setToast(error instanceof Error ? error.message : '操作暂时无法完成，请重试。');
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!api || !me) return;
    const timer = window.setInterval(() => {
      if (document.hidden || !docs.some(doc => doc.job && !['READY', 'COMPLETED', 'FAILED', 'CANCELLED', 'DELETING', 'DELETED'].includes(doc.job.status))) return;
      void refresh().catch(onError);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [api, me, docs, refresh, onError]);

  const login = async (username: string, password: string) => {
    if (!username || !password) return;
    setLoggingIn(true); setLoginError('');
    try {
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }), cache: 'no-store',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { message?: string };
        throw new Error(body.message || '用户名或密码错误。');
      }
      const result = await response.json() as { access_token: string };
      if (!result.access_token) throw new Error('登录响应无效。');
      sessionStorage.setItem('zg.session.v2', result.access_token);
      setToken(result.access_token);
    } catch (error) { setLoginError(error instanceof Error ? error.message : '登录失败，请重试。'); }
    finally { setLoggingIn(false); }
  };
  const context = api && me && status ? {
    api, me, kbs, docs, sessions, status, selectedKB, setSelectedKB, refresh, notify, onError, logout,
    openNewKB: () => setNewKB(true), openPreview: (doc: Pick<DocumentRecord, 'id' | 'name'>) => setPreview(doc),
  } satisfies WorkspaceContextValue : null;

  if (!context) return <><div id="startup" hidden={!loading}>正在连接掌柜智库…</div>
    {!token && <Login onLogin={login} error={loginError} busy={loggingIn} />}</>;

  return <WorkspaceContext.Provider value={context}>{children}
    {newKB && <NewKnowledgeBase api={context.api} onClose={() => setNewKB(false)} onError={onError}
      onCreated={async id => { await refresh(); setSelectedKB(id); setNewKB(false); notify('知识库已创建，可以上传第一份文档了。'); }} />}
    {preview && <DocumentPreview api={context.api} doc={preview} onClose={() => setPreview(null)} />}
    {toast && <div className="toast" role="status">{toast}</div>}
  </WorkspaceContext.Provider>;
}
