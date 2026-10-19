import { useNavigate } from 'react-router-dom';
import { Button, Empty, PageHead, date } from '../components';
import { useWorkspace } from '../workspace';

export default function Knowledge() {
  const { kbs, docs, setSelectedKB, openNewKB } = useWorkspace();
  const navigate = useNavigate();
  const go = (id: string, path: string) => { setSelectedKB(id); navigate(path); };

  return <>
    <PageHead title="知识库" description="按主题汇聚团队知识，建立清晰、可信的知识空间。">
      <Button className="primary" onClick={openNewKB}>＋ 新建知识库</Button>
    </PageHead>
    {kbs.length === 0 ? <div className="panel"><Empty title="还没有知识库" description="创建一个知识库，开始整理属于团队的知识。">
      <Button className="primary" onClick={openNewKB}>创建知识库</Button>
    </Empty></div> : <div className="kb-grid">{kbs.map(kb => {
      const entries = docs.filter(doc => doc.kb_id === kb.id && !doc.deleted_at);
      return <article className="kb-card" key={kb.id}>
        <div className="kb-symbol" aria-hidden="true">▣</div>
        <h3>{kb.name}</h3>
        <p>{entries.length} 份文档 · {entries.filter(doc => Boolean(doc.current_version_id)).length} 份可检索</p>
        <p>创建于 {date(kb.created_at)}</p>
        <div className="actions"><Button className="subtle" onClick={() => go(kb.id, '/documents')}>管理文档 →</Button>
          <Button onClick={() => go(kb.id, '/chat')}>提问</Button></div>
      </article>;
    })}</div>}
  </>;
}
