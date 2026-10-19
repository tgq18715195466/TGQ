import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Button, Empty, Modal, PageHead, terminal } from '../components';
import { SSEParser, type SSEFrame } from '../sse';
import type { AnswerCitation, ChatHistory, ChatSession, Message } from '../types';
import { useWorkspace } from '../workspace';

const stages: Record<string, string> = {
  route: '分析问题', retrieve: '检索知识', rerank: '筛选证据',
  generate: '组织答案', verify: '核验引用',
};
const suggestions = ['总结文档中的关键要点', '这份资料有哪些注意事项？', '请帮我梳理相关操作流程', '比较相关文档中的不同观点'];
const emptyHistory = (): ChatHistory => ({ messages: [], runs: [], citations: [] });

interface ActiveRun {
  id: string;
  sessionId: string;
  text: string;
  status: string;
  last: string;
  citations: AnswerCitation[];
  controller?: AbortController;
  failed?: boolean;
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function MessageCard({ message, name, citations, onCitations, onFeedback }: {
  message: Message; name: string; citations: AnswerCitation[];
  onCitations: (citations: AnswerCitation[]) => void;
  onFeedback: (runId: string, rating: number) => void;
}) {
  return <article className={`message ${message.role}`}>
    <div className="message-avatar">{message.role === 'user' ? '我' : '✦'}</div>
    <div className="message-main"><div className="message-name">{message.role === 'user' ? name : '掌柜智库'}</div>
      <div className="message-text">{message.content}</div>
      {message.role === 'assistant' && message.run_id && <div className="message-tools">
        <Button onClick={() => onCitations(citations)}>引用来源 {citations.length}</Button>
        <Button onClick={() => onFeedback(message.run_id!, 1)}>有帮助</Button>
        <Button onClick={() => onFeedback(message.run_id!, -1)}>需改进</Button>
      </div>}
    </div>
  </article>;
}

export default function Chat() {
  const { api, me, kbs, sessions, status, selectedKB, setSelectedKB, refresh, notify, onError, openPreview } = useWorkspace();
  const [session, setSession] = useState<ChatSession | null>(null);
  const [history, setHistory] = useState<ChatHistory | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [question, setQuestion] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [active, setActive] = useState<ActiveRun | null>(null);
  const [paused, setPaused] = useState<Set<string>>(() => new Set());
  const [citations, setCitations] = useState<AnswerCitation[] | null>(null);
  const [feedback, setFeedback] = useState<{ runId: string; rating: number } | null>(null);
  const [comment, setComment] = useState('');
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [messageError, setMessageError] = useState('');
  const activeRef = useRef<ActiveRun | null>(null);
  const sessionRef = useRef<ChatSession | null>(null);
  const navigationRef = useRef(0);
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      navigationRef.current++;
      activeRef.current?.controller?.abort();
      activeRef.current = null;
    };
  }, []);
  useEffect(() => { if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight; }, [history, active, loadingHistory]);

  const showActive = (run: ActiveRun | null) => {
    activeRef.current = run;
    if (mountedRef.current) setActive(run ? { ...run } : null);
  };
  const selectedSession = (value: ChatSession | null) => {
    sessionRef.current = value;
    setSession(value);
  };
  const detachActive = () => {
    activeRef.current?.controller?.abort();
    showActive(null);
  };
  const newSession = () => {
    navigationRef.current++;
    detachActive();
    selectedSession(null);
    setHistory(null);
    setLoadingHistory(false);
    setMessageError('');
  };
  const loadHistory = async (target: ChatSession, navigation = navigationRef.current): Promise<ChatHistory | null> => {
    try {
      const result = await api.request<ChatHistory>(`/chat/sessions/${target.id}/messages`);
      if (!mountedRef.current || navigation !== navigationRef.current || sessionRef.current?.id !== target.id) return null;
      setHistory(result);
      setMessageError('');
      return result;
    } catch (error) {
      if (!mountedRef.current || navigation !== navigationRef.current || isAbort(error)) return null;
      onError(error);
      setMessageError(error instanceof Error ? error.message : '读取会话失败');
      return null;
    } finally {
      if (mountedRef.current && navigation === navigationRef.current) setLoadingHistory(false);
    }
  };
  const openSession = (target: ChatSession) => {
    const navigation = ++navigationRef.current;
    detachActive();
    selectedSession(target);
    setHistory(null);
    setMessageError('');
    setLoadingHistory(true);
    setPaused(new Set());
    void loadHistory(target, navigation);
  };

  const receive = (frame: SSEFrame, run: ActiveRun): boolean => {
    if (activeRef.current !== run) return false;
    if (frame.id && /^\d+$/.test(frame.id) && /^\d+$/.test(run.last) && Number(frame.id) <= Number(run.last)) return false;
    let data: Record<string, unknown>;
    try { data = JSON.parse(frame.data) as Record<string, unknown>; } catch { return false; }
    if (frame.id) run.last = frame.id;
    if (frame.type === 'answer.delta' && typeof data.text === 'string') run.text += data.text;
    if (frame.type === 'answer.replace' && typeof data.text === 'string') run.text = data.text;
    if (frame.type === 'citation.ready') run.citations.push(data as unknown as AnswerCitation);
    if (frame.type === 'run.stage') run.status = stages[String(data.stage)] || `正在处理：${String(data.stage)}`;
    if (frame.type === 'run.failed') {
      run.status = data.error_code === 'CANCELLED' ? '生成已停止。' : `生成失败：${String(data.error_code || '未知错误')}`;
      run.failed = true;
    }
    if (frame.type === 'run.completed' && data.error_code) {
      run.status = data.error_code === 'INSUFFICIENT_EVIDENCE' ? '当前资料不足以支持回答，请补充相关文档。' : String(data.error_code);
    }
    showActive(run);
    return frame.type === 'run.completed' || frame.type === 'run.failed';
  };

  const stream = async (run: ActiveRun) => {
    let done = false;
    for (let attempt = 0; attempt < 4 && !done && activeRef.current === run && mountedRef.current; attempt++) {
      const controller = new AbortController();
      run.controller = controller;
      try {
        const response = await api.stream(`/chat/runs/${run.id}/events`, run.last, controller.signal);
        if (!response.body) throw new Error('回答连接没有返回数据');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const parser = new SSEParser(frame => { if (receive(frame, run)) done = true; }, run.last);
        try {
          while (activeRef.current === run && mountedRef.current) {
            const { value, done: ended } = await reader.read();
            parser.feed(decoder.decode(value || new Uint8Array(), { stream: !ended }), ended);
            if (done || ended) break;
          }
        } finally { if (done || activeRef.current !== run || !mountedRef.current) await reader.cancel().catch(() => {}); }
        if (!done && activeRef.current === run) throw new Error('回答连接已中断');
      } catch (error) {
        if (isAbort(error) || activeRef.current !== run || !mountedRef.current) return;
        if (attempt === 3) {
          run.status = '连接已中断。可重新连接回答。';
          run.failed = true;
          setPaused(previous => new Set(previous).add(run.id));
          notify(run.status);
          showActive(run);
          break;
        }
        run.status = `连接中断，正在恢复（${attempt + 1}/3）…`;
        showActive(run);
        await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
      }
    }
    if (!mountedRef.current || activeRef.current !== run) return;
    showActive(null);
    if (sessionRef.current?.id === run.sessionId) {
      setLoadingHistory(true);
      const latest = await loadHistory(sessionRef.current, navigationRef.current);
      if (run.failed && latest && !latest.runs.some(item => item.id === run.id && terminal.has(item.status))
        && !latest.messages.some(item => item.role === 'assistant' && item.run_id === run.id)) setMessageError(run.status);
    }
  };

  useEffect(() => {
    if (!session || !history || activeRef.current) return;
    const pending = history.runs.find(run => !terminal.has(run.status) && !paused.has(run.id));
    if (!pending) return;
    const run: ActiveRun = { id: pending.id, sessionId: session.id, text: '', citations: [], last: '0', status: '正在恢复回答…' };
    showActive(run);
    void stream(run).catch(onError);
  // Stream is deliberately tied to the loaded history, not to changing context values.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, history, paused]);

  const submitQuestion = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = question.trim();
    if (!text || activeRef.current || submittingRef.current) return;
    const kb = selectedKB || kbs[0]?.id;
    if (!kb) { notify('请先选择知识库。'); return; }
    submittingRef.current = true;
    setSubmitting(true);
    const navigation = navigationRef.current;
    let target = sessionRef.current;
    let nextHistory = history || emptyHistory();
    try {
      if (!target) {
        target = await api.request<ChatSession>('/chat/sessions', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: text.slice(0, 60) }),
        });
        nextHistory = emptyHistory();
        if (!mountedRef.current) return;
        void refresh().catch(onError);
        if (navigation === navigationRef.current && !sessionRef.current) {
          selectedSession(target);
          setHistory(nextHistory);
        }
      }
      if (navigation !== navigationRef.current || sessionRef.current?.id !== target.id) return;
      const result = await api.request<{ run_id: string }>('/chat/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: target.id, question: text, kb_ids: [kb], web_search_enabled: false }),
      });
      if (!mountedRef.current) return;
      if (navigation !== navigationRef.current || sessionRef.current?.id !== target.id) return;
      const run: ActiveRun = { id: result.run_id, sessionId: target.id, text: '', citations: [], last: '0', status: '正在等待知识检索…' };
      if (sessionRef.current?.id === target.id) {
        setHistory({ ...nextHistory, messages: [...nextHistory.messages, { role: 'user', content: text, run_id: result.run_id }] });
        setQuestion('');
      }
      showActive(run);
      void stream(run).catch(onError);
    } catch (error) { if (mountedRef.current) onError(error); }
    finally { submittingRef.current = false; if (mountedRef.current) setSubmitting(false); }
  };
  const cancelActive = async () => {
    const run = activeRef.current;
    if (!run) return;
    try {
      await api.mutate(`/chat/runs/${run.id}/cancel`);
      if (!mountedRef.current || activeRef.current !== run) return;
      run.controller?.abort();
      showActive(null);
      setPaused(previous => new Set(previous).add(run.id));
      if (sessionRef.current?.id === run.sessionId) {
        setLoadingHistory(true);
        await loadHistory(sessionRef.current, navigationRef.current);
      }
      notify('已停止生成。');
    } catch (error) { if (mountedRef.current) onError(error); }
  };
  const submitFeedback = async (event: FormEvent) => {
    event.preventDefault();
    if (!feedback || feedbackBusy) return;
    setFeedbackBusy(true);
    try {
      await api.mutate(`/chat/runs/${feedback.runId}/feedback`, { rating: feedback.rating, comment });
      if (!mountedRef.current) return;
      setFeedback(null); setComment(''); notify('感谢反馈，已记录你的意见。');
    } catch (error) { if (mountedRef.current) onError(error); }
    finally { if (mountedRef.current) setFeedbackBusy(false); }
  };
  const onQuestionKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
      event.preventDefault();
      if (!submittingRef.current && !activeRef.current) void submitQuestion();
    }
  };
  const visibleActive = active?.sessionId === session?.id ? active : null;
  const messages = history?.messages || [];
  const failedRuns = new Map((history?.runs || []).map(run => [run.id, run]));

  return <>
    <PageHead title="智能问答" description="向团队知识提问。每一份可信答案，都可以追溯到来源。" />
    {!status.external_services_verified && <div className="notice">解析、索引和模型服务尚未完成联调。问题会保存为后台任务，回答需相关服务就绪后生成。</div>}
    <div className="chat-layout"><aside className="chat-history">
      <Button className="primary wide" onClick={newSession}>＋ 开启新会话</Button><h3>最近会话</h3>
      {sessions.map(item => <Button key={item.id} className={`session-button${session?.id === item.id ? ' active' : ''}`} onClick={() => openSession(item)}>{item.title}</Button>)}
      {!sessions.length && <p className="hint">你的会话将保存在这里。</p>}
    </aside><section className="chat-content">
      <div className="chat-top"><span className="muted">知识范围</span><select id="chat-kb" aria-label="选择知识库" value={selectedKB || kbs[0]?.id || ''} onChange={event => setSelectedKB(event.target.value)}>
        {kbs.map(kb => <option key={kb.id} value={kb.id}>{kb.name}</option>)}
      </select><span className="hint">基于已发布文档回答</span></div>
      <div className="messages" id="messages" aria-live="polite" ref={messagesRef}>
        {loadingHistory && <div className="loading">正在读取会话…</div>}
        {!loadingHistory && messages.map((message, index) => {
          const source = (history?.citations || []).filter(citation => citation.run_id === message.run_id);
          const run = message.run_id ? failedRuns.get(message.run_id) : null;
          const showFailure = message.role === 'user' && run && ['FAILED', 'CANCELLED'].includes(run.status)
            && !messages.some(answer => answer.role === 'assistant' && answer.run_id === run.id);
          return <div key={message.id || `${message.run_id}-${index}`}>
            <MessageCard message={message} name={me.name || '我'} citations={source} onCitations={setCitations} onFeedback={(runId, rating) => { setComment(''); setFeedback({ runId, rating }); }} />
            {showFailure && <MessageCard message={{ role: 'assistant', content: run.status === 'CANCELLED' ? '生成已停止。' : `生成失败：${run.error_code || '请检查服务状态'}`, run_id: null }} name={me.name} citations={[]} onCitations={setCitations} onFeedback={() => {}} />}
          </div>;
        })}
        {!loadingHistory && !messages.length && !visibleActive && <div className="chat-welcome"><div className="empty-symbol">✦</div><h2>你好，今天想了解什么？</h2><p>从团队知识中检索信息、梳理重点，找到有据可循的答案。</p>
          <div className="suggestions">{suggestions.map(text => <Button key={text} onClick={() => { setQuestion(text); questionRef.current?.focus(); }}>{text}</Button>)}</div>
        </div>}
        {!loadingHistory && visibleActive && <MessageCard message={{ role: 'assistant', content: visibleActive.text || visibleActive.status, run_id: null }} name={me.name} citations={[]} onCitations={setCitations} onFeedback={() => {}} />}
        {!loadingHistory && messageError && <div className="error-box" role="alert">{messageError}</div>}
        {!loadingHistory && history?.runs.some(run => paused.has(run.id)) && <Button onClick={() => {
          setPaused(previous => { const next = new Set(previous); history.runs.forEach(run => next.delete(run.id)); return next; });
        }}>重新连接回答</Button>}
      </div>
      <form className="composer" onSubmit={event => { void submitQuestion(event); }}><div className="composer-box">
        <textarea id="question" aria-label="输入问题" ref={questionRef} rows={2} required maxLength={8000} value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={onQuestionKey}
          disabled={!kbs.length} placeholder={kbs.length ? '输入问题，让团队知识为你解答…' : '请先创建知识库并上传文档'} />
        <div className="composer-bottom"><span>Enter 发送 · Shift + Enter 换行</span><div className="actions">
          {active && <Button id="stop-question" onClick={cancelActive}>停止生成</Button>}
          <Button id="send-question" type="submit" className="primary" disabled={!kbs.length || !!active || submitting}>发送 ↑</Button>
        </div></div>
      </div><div className="composer-hint">回答基于当前知识库内容，重要信息请核验原文。</div></form>
    </section></div>
    {citations && <Modal title="答案的知识来源" drawer onClose={() => setCitations(null)}>
      {!citations.length && <Empty title="暂无可展示的引用" description="该回答没有已保存的来源片段。" />}
      {citations.map((citation, index) => <section className="chunk" key={citation.id || `${citation.document_id}-${index}`}>
        <div className="chunk-label">引用 {index + 1} · 第 {citation.page_start}–{citation.page_end} 页</div><h3>{citation.document_name}</h3>
        <p className="hint">{(citation.section_path || []).join(' / ')}</p><div className="raw-text">{citation.source_excerpt}</div>
        <Button className="subtle" onClick={() => openPreview({ id: citation.document_id, name: citation.document_name })}>查看来源文档 →</Button>
      </section>)}
    </Modal>}
    {feedback && <Modal title={feedback.rating === 1 ? '这个回答有帮助' : '帮助改进这个回答'} onClose={() => setFeedback(null)}>
      <form onSubmit={event => { void submitFeedback(event); }}><label htmlFor="feedback-comment">补充意见（可选）</label>
        <textarea id="feedback-comment" className="wide" rows={3} maxLength={8000} value={comment} onChange={event => setComment(event.target.value)} placeholder="告诉我们哪些地方有帮助，或需要改进" />
        <div className="dialog-actions"><Button className="primary" type="submit" disabled={feedbackBusy}>提交反馈</Button></div>
      </form>
    </Modal>}
  </>;
}
