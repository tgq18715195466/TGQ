import { useNavigate } from 'react-router-dom';
import { Badge, Button, Empty, PageHead, statusOf, terminal } from '../components';
import { useWorkspace } from '../workspace';

export default function Overview() {
  const { kbs, docs, openNewKB, openPreview } = useWorkspace();
  const navigate = useNavigate();
  const recent = [...docs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 5);
  const metrics = [
    ['知识库', kbs.length, '已加入的知识空间', '▣'],
    ['文档总数', docs.length, '当前可访问的文档', '▤'],
    ['可检索文档', docs.filter(doc => Boolean(doc.current_version_id)).length, '已有发布版本的文档', '✓'],
    ['处理中任务', docs.filter(doc => !terminal.has(statusOf(doc))).length, '上传、解析与索引任务', '◴'],
  ] as const;
  return <>
    <PageHead title="知识，让工作更进一步" description="欢迎回到掌柜智库。管理团队知识，获取可信答案。">
      <Button onClick={openNewKB}>＋ 新建知识库</Button><Button className="primary" onClick={() => navigate('/documents')}>↑ 上传文档</Button>
    </PageHead>
    <section className="hero"><span className="eyebrow">YOUR TEAM’S KNOWLEDGE, CONNECTED</span>
      <h2>把分散的知识，变成团队的答案</h2>
      <p>从文档中寻找线索，让每一个回答都有来源。让知识沉淀，让协作发生。</p>
      <Button className="primary" onClick={() => navigate('/chat')}>开始智能问答 →</Button>
      <div className="hero-art" aria-hidden="true"><div className="orbit" /><div className="book">知</div><span className="hero-spark">✦</span></div>
    </section>
    <div className="metrics">{metrics.map(([label, value, note, icon]) => <div className="metric" key={label}>
      <div className="metric-top"><span>{label}</span><span className="metric-icon">{icon}</span></div>
      <div className="metric-value">{value}</div><small>{note}</small>
    </div>)}</div>
    <div className="overview-grid"><section className="panel">
      <div className="section-head"><div><h2>最近更新的文档</h2><p>团队知识，在这里持续积累</p></div>
        <Button className="subtle" onClick={() => navigate('/documents')}>查看全部 →</Button></div>
      {recent.length ? <div className="table-wrap"><table><thead><tr><th>文档名称</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{recent.map(doc => <tr key={doc.id}><td><div className="file-title"><span className="file-icon">{doc.name.split('.').pop()?.slice(0, 4).toUpperCase() || 'FILE'}</span>
          <div><Button className="file-name" onClick={() => openPreview(doc)}>{doc.name}</Button><span className="subline">版本 {doc.latest_version?.version_no || 1}</span></div></div></td>
          <td><Badge status={statusOf(doc)} /></td><td><Button onClick={() => openPreview(doc)}>预览</Button></td></tr>)}</tbody>
      </table></div> : <Empty title="知识从第一份文档开始" description="上传制度、手册或项目资料，建立可检索的团队记忆。">
        <Button onClick={() => navigate('/documents')}>上传第一份文档</Button></Empty>}
    </section><section className="panel padded"><div className="section-head"><div><h2>三步，开启知识之旅</h2><p>让信息更有序，让答案更可靠</p></div></div>
      {([
        ['建立知识空间', '按业务或主题创建知识库，让团队资料井然有序。'],
        ['汇集团队文档', '上传原始文件，等待解析、分块和索引完成。'],
        ['开始有据问答', '选择知识范围，提问并回溯每一条引用。'],
      ] as const).map(([title, text], index) => <div className="step" key={title}><span className="step-num">{String(index + 1).padStart(2, '0')}</span>
        <div><strong>{title}</strong><p>{text}</p></div></div>)}</section></div>
  </>;
}
