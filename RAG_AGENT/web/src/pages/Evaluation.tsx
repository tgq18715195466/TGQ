import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Badge, Button, date, Empty, Modal, PageHead, terminal } from '../components';
import type { EvalDataset, EvalRun } from '../types';
import { useWorkspace } from '../workspace';

export default function Evaluation() {
  const { api, kbs, selectedKB, onError, notify } = useWorkspace();
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [datasetId, setDatasetId] = useState('');
  const [kbId, setKbId] = useState(selectedKB || kbs[0]?.id || '');
  const [selected, setSelected] = useState<EvalRun | null>(null);
  const [detail, setDetail] = useState<EvalRun | null>(null);
  const detailRequest = useRef(0);

  const load = async () => {
    const [available, current] = await Promise.all([
      api.request<EvalDataset[]>('/eval-datasets'), api.request<EvalRun[]>('/eval-runs'),
    ]);
    setDatasets(available); setRuns(current); setDatasetId(id => id || available[0]?.id || ''); setLoading(false);
  };
  useEffect(() => {
    let live = true;
    void Promise.all([api.request<EvalDataset[]>('/eval-datasets'), api.request<EvalRun[]>('/eval-runs')])
      .then(([available, current]) => { if (live) { setDatasets(available); setRuns(current); setDatasetId(available[0]?.id || ''); setLoading(false); } })
      .catch(error => { if (live) { onError(error); setLoading(false); } });
    return () => { live = false; };
  }, [api, onError]);
  useEffect(() => {
    if (!runs.some(run => !terminal.has(run.status))) return;
    const timer = window.setInterval(() => { if (!document.hidden) void load().catch(onError); }, 10000);
    return () => window.clearInterval(timer);
  }, [runs, api, onError]);
  const start = async (event: FormEvent) => {
    event.preventDefault(); if (!datasetId || !kbId || busy) return;
    setBusy(true);
    try {
      await api.request<EvalRun>('/eval-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset_id: datasetId, kb_ids: [kbId], use_ragas: false }) });
      setCreating(false); await load(); notify('评估任务已提交。');
    } catch (error) { onError(error); } finally { setBusy(false); }
  };
  const openDetail = async (run: EvalRun) => {
    const request = ++detailRequest.current;
    setSelected(run); setDetail(null);
    try {
      const result = await api.request<EvalRun>(`/eval-runs/${run.id}`);
      if (request === detailRequest.current) setDetail(result);
    } catch (error) { if (request === detailRequest.current) onError(error); }
  };
  return <><PageHead title="质量评估" description="用真实数据检查检索与回答表现，持续提升知识服务质量。">
    <Button onClick={() => load().catch(onError)}>刷新</Button>
    <Button className="primary" onClick={() => datasets.length ? setCreating(true) : notify('尚无可用评估数据集，请先由管理员导入。')}>＋ 创建评估</Button>
  </PageHead>
    <div className="notice">可用评估数据集 {datasets.length} 个。评估任务在后台执行，结果仅显示服务返回的实测指标。</div>
    {loading ? <div className="loading">正在载入评估记录…</div> : runs.length ? <div className="panel table-wrap"><table><thead><tr>
      <th>数据集</th><th>状态</th><th>创建时间</th><th>结果</th></tr></thead><tbody>
      {runs.map(run => <tr key={run.id}><td>{datasets.find(item => item.id === run.dataset_id)?.name || run.dataset_id}</td>
        <td><Badge status={run.status} /></td><td className="muted">{date(run.created_at)}</td>
        <td><Button className="subtle" onClick={() => { void openDetail(run); }}>查看结果</Button></td></tr>)}</tbody></table></div>
      : <section className="panel"><Empty title="还没有评估记录" description={datasets.length ? '选择数据集与知识库，开始第一次质量评估。' : '尚无可用数据集。请管理员通过数据导入流程准备评估数据。'}>
        {datasets.length > 0 && <Button onClick={() => setCreating(true)}>创建评估</Button>}</Empty></section>}
    {creating && <Modal title="创建质量评估" onClose={() => setCreating(false)}><form onSubmit={event => { void start(event); }}>
      <label htmlFor="eval-dataset">评估数据集</label><select className="wide" id="eval-dataset" value={datasetId} onChange={event => setDatasetId(event.target.value)}>
        {datasets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.version}</option>)}</select>
      <label htmlFor="eval-kb">知识库</label><select className="wide" id="eval-kb" value={kbId} onChange={event => setKbId(event.target.value)}>
        {kbs.map(kb => <option key={kb.id} value={kb.id}>{kb.name}</option>)}</select>
      <p className="field-help">需要所选知识库的管理权限。任务提交后由后台工作进程执行。</p>
      <div className="dialog-actions"><Button onClick={() => setCreating(false)}>取消</Button><button className="primary" type="submit" disabled={busy}>开始评估</button></div>
    </form></Modal>}
    {selected && <Modal title="评估结果" drawer onClose={() => { detailRequest.current++; setSelected(null); setDetail(null); }}>
      {!detail ? <div className="loading">正在读取结果…</div> : <><Badge status={detail.status} />
        <p className="hint">创建于 {date(detail.created_at)}</p>{detail.error_code && <p className="error">{detail.error_code}</p>}
        {Object.entries(detail.metrics_jsonb || {}).length ? Object.entries(detail.metrics_jsonb).map(([key, value]) => <div className="status-row" key={key}>
          <span>{key}</span><span>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span></div>)
          : <Empty title="评估结果尚未生成" description="任务完成后，实测指标将显示在这里。" />}
        <Button onClick={() => { void openDetail(selected); }}>刷新结果</Button></>}
    </Modal>}
  </>;
}
