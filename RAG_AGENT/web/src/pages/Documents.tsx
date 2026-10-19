import { useRef, useState, type ChangeEvent, type DragEvent, type KeyboardEvent } from 'react';
import { Badge, Button, Empty, Modal, PageHead, date, statusOf, terminal } from '../components';
import type { DocumentRecord } from '../types';
import { useWorkspace } from '../workspace';

const mimeTypes: Record<string, string> = {
  pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  tif: 'image/tiff', tiff: 'image/tiff', bmp: 'image/bmp',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  txt: 'text/plain', md: 'text/markdown', html: 'text/html', htm: 'text/html',
};
const stages: Record<string, string> = {
  validate_file: '校验文件', scan_file: '安全扫描', normalize_file: '格式转换',
  parse_with_mineru: '解析文档', normalize_parse_result: '整理内容',
  chunk_document: '切分文本', embed_chunks: '生成向量', extract_graph: '提取关系',
  sync_indexes: '同步索引', publish_version: '发布版本',
};
const fileType = (name: string) => mimeTypes[name.split('.').pop()?.toLowerCase() || ''];
const uploadAccept = Object.keys(mimeTypes).map(type => `.${type}`).join(',');

export default function Documents() {
  const { api, kbs, docs, selectedKB, setSelectedKB, refresh, notify, onError, openNewKB, openPreview } = useWorkspace();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<File[]>([]);
  const [uploadKB, setUploadKB] = useState('');
  const [deleteDoc, setDeleteDoc] = useState<DocumentRecord | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const kbName = (id: string) => kbs.find(kb => kb.id === id)?.name || '知识库';

  const run = async (action: () => Promise<unknown>, success: string) => {
    try { await action(); await refresh(); notify(success); }
    catch (error) { onError(error); }
  };

  const upload = async (files: File[], kbId: string) => {
    if (busy || !files.length) return;
    setBusy(true);
    let count = 0;
    try {
      for (const file of files) {
        const mime = fileType(file.name);
        if (!mime) throw new Error(`不支持此文件格式：${file.name}`);
        notify(`正在上传 ${count + 1}/${files.length}：${file.name}`);
        const body = new FormData();
        body.append('file', new Blob([file], { type: mime }), file.name);
        await api.request(`/kbs/${kbId}/documents`, {
          method: 'POST', body, headers: { 'Idempotency-Key': crypto.randomUUID() },
        });
        count += 1;
      }
      notify(`${count} 份文档上传完成，后台处理任务已排队。`);
    } catch (error) { onError(error); }
    finally { setBusy(false); await refresh().catch(onError); }
  };

  const prepareUpload = (files: File[]) => {
    if (!files.length || busy) return;
    if (selectedKB) { void upload(files, selectedKB); return; }
    setPending(files);
    setUploadKB(kbs[0]?.id || '');
  };
  const onFiles = (event: ChangeEvent<HTMLInputElement>) => {
    prepareUpload(Array.from(event.target.files || []));
    event.target.value = '';
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault(); setDrag(false);
    prepareUpload(Array.from(event.dataTransfer.files));
  };
  const onDropKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); input.current?.click(); }
  };
  const download = async (doc: DocumentRecord) => {
    try {
      const blob = await api.download(`/documents/${doc.id}/download`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = doc.name; document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { onError(error); }
  };
  const visible = docs.filter(doc => !doc.deleted_at && (!selectedKB || doc.kb_id === selectedKB)
    && doc.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())
    && (!filter || (filter === 'active' ? !terminal.has(statusOf(doc))
      : filter === 'failed' ? ['FAILED', 'CANCELLED'].includes(statusOf(doc)) : statusOf(doc) === filter)));

  return <>
    <PageHead title="文档管理" description="管理原始资料，跟踪解析进度，让知识保持新鲜。">
      <Button onClick={async () => { try { await refresh(); notify('文档列表已更新。'); } catch (error) { onError(error); } }}>刷新</Button>
      <Button className="primary" onClick={openNewKB}>＋ 新建知识库</Button>
    </PageHead>
    {kbs.length === 0 ? <div className="panel"><Empty title="先为文档建立一个家" description="创建知识库后，即可上传文档并跟踪处理进度。">
      <Button className="primary" onClick={openNewKB}>创建知识库</Button>
    </Empty></div> : <>
      <div className="toolbar">
        <input aria-label="搜索文档" placeholder="搜索文档名称…" value={search} onChange={event => setSearch(event.target.value)} />
        <select aria-label="筛选知识库" value={selectedKB} onChange={event => setSelectedKB(event.target.value)}>
          <option value="">全部知识库</option>{kbs.map(kb => <option value={kb.id} key={kb.id}>{kb.name}</option>)}
        </select>
        <select aria-label="筛选文档状态" value={filter} onChange={event => setFilter(event.target.value)}>
          <option value="">全部状态</option><option value="READY">已就绪</option><option value="active">处理中</option><option value="failed">失败或取消</option>
        </select>
      </div>
      <div className={`dropzone${drag ? ' drag' : ''}`} role="button" tabIndex={0} aria-label="选择文件上传"
        onClick={() => { if (!busy) input.current?.click(); }} onKeyDown={onDropKey}
        onDragEnter={event => { event.preventDefault(); setDrag(true); }}
        onDragOver={event => event.preventDefault()} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
        <div className="upload-symbol" aria-hidden="true">↥</div><strong>拖放文件到此处，或点击上传</strong>
        <p>支持 PDF、DOCX、XLSX、PPTX、TXT、Markdown、HTML 及常见图片</p>
        <p>{selectedKB ? `上传到「${kbName(selectedKB)}」` : '上传时选择目标知识库'}</p>
      </div>
      <input ref={input} type="file" multiple hidden accept={uploadAccept} onChange={onFiles} />
      {busy && <div className="upload-queue" role="status">正在上传文档…</div>}
      <div className="panel">{visible.length === 0 ? <Empty title="暂无符合条件的文档" description="上传文档，或调整筛选条件后重试。" /> : <div className="table-wrap"><table>
        <thead><tr><th>文档名称</th><th>所属知识库</th><th>状态 / 进度</th><th>上传时间</th><th>操作</th></tr></thead>
        <tbody>{visible.map(doc => <tr key={doc.id}>
          <td className="name-cell"><div className="file-title"><span className="file-icon" aria-hidden="true">{(doc.name.split('.').pop() || 'FILE').slice(0, 4).toUpperCase()}</span>
            <div><Button className="file-name" onClick={() => openPreview(doc)}>{doc.name}</Button>
              <span className="subline">{doc.latest_version ? `版本 ${doc.latest_version.version_no}` : '原始文档'}</span></div></div></td>
          <td>{kbName(doc.kb_id)}</td><td><Badge status={statusOf(doc)} />
            {doc.job && !terminal.has(statusOf(doc)) && <><progress max={1} value={Math.max(0, Math.min(1, doc.job.progress || 0))} aria-label="文档处理进度" />
              <span className="subline">{stages[doc.job.current_stage] || doc.job.current_stage}</span></>}
            {doc.job?.error_code && <span className="subline">{doc.job.error_code}</span>}</td>
          <td className="muted">{date(doc.created_at)}</td><td><div className="row-actions">
            <Button onClick={() => openPreview(doc)}>预览</Button>
            <Button onClick={() => download(doc)}>下载</Button>
            {doc.job && ['FAILED', 'CANCELLED'].includes(doc.job.status) && doc.latest_version && <Button onClick={() => run(
              () => api.mutate(`/document-versions/${doc.latest_version!.id}/retry`, { stage: doc.job!.current_stage || 'validate_file' }), '已提交重试任务。',
            )}>重试</Button>}
            {doc.job && !terminal.has(doc.job.status) && <Button onClick={() => run(() => api.mutate(`/jobs/${doc.job!.id}/cancel`), '已请求取消处理。')}>取消</Button>}
            <Button className="danger" onClick={() => setDeleteDoc(doc)}>删除</Button>
          </div></td>
        </tr>)}</tbody>
      </table></div>}</div>
    </>}
    {pending.length > 0 && <Modal title="上传文档" onClose={() => setPending([])}><p className="muted">已选择 {pending.length} 个文件，请选择目标知识库。</p>
      <select className="wide" aria-label="上传目标知识库" value={uploadKB} onChange={event => setUploadKB(event.target.value)}>
        {kbs.map(kb => <option value={kb.id} key={kb.id}>{kb.name}</option>)}
      </select><div className="dialog-actions"><Button onClick={() => setPending([])}>取消</Button>
        <Button className="primary" disabled={!uploadKB} onClick={() => {
          const files = pending; setPending([]); setSelectedKB(uploadKB); void upload(files, uploadKB);
        }}>开始上传</Button></div></Modal>}
    {deleteDoc && <Modal title="删除文档" onClose={() => setDeleteDoc(null)}>
      <p>确定删除「{deleteDoc.name}」吗？</p><p className="muted">文档将从知识库移除，并停止参与后续检索。</p>
      <div className="dialog-actions"><Button onClick={() => setDeleteDoc(null)}>保留文档</Button>
        <Button className="primary" onClick={async () => {
          const doc = deleteDoc; setDeleteDoc(null);
          await run(() => api.mutate(`/documents/${doc.id}`, {}, 'DELETE'), '文档已移除，索引清理任务已排队。');
        }}>删除文档</Button></div>
    </Modal>}
  </>;
}
