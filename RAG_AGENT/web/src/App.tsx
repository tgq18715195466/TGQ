import { useEffect } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Chat from './pages/Chat';
import Documents from './pages/Documents';
import Evaluation from './pages/Evaluation';
import Knowledge from './pages/Knowledge';
import Overview from './pages/Overview';
import Settings from './pages/Settings';
import { useWorkspace } from './workspace';

const navigation = [
  { path: '/overview', label: '工作台', icon: '◫' },
  { path: '/knowledge', label: '知识库', icon: '▣' },
  { path: '/documents', label: '文档管理', icon: '▤' },
  { path: '/chat', label: '智能问答', icon: '✦' },
  { path: '/evaluation', label: '质量评估', icon: '◴' },
];

export default function App() {
  const { me, logout } = useWorkspace();
  const location = useLocation();
  const current = [...navigation, { path: '/settings', label: '系统设置', icon: '⚙' }]
    .find(item => item.path === location.pathname)?.label || '工作台';
  useEffect(() => { document.title = `掌柜智库 · ${current}`; }, [current]);
  return <div className="shell"><aside className="sidebar">
    <NavLink className="brand" to="/overview"><span className="brand-mark">掌</span><div>掌柜智库<small>KNOWLEDGE WORKSPACE</small></div></NavLink>
    <div className="workspace"><span className="workspace-icon">企</span><div>企业知识空间<small>团队工作台</small></div><span className="muted">⌄</span></div>
    <p className="nav-label">工作空间</p><nav aria-label="主导航">{navigation.map(item => <NavLink key={item.path} className={({ isActive }) => isActive ? 'active' : ''} to={item.path}>
      <span aria-hidden="true">{item.icon}</span>{item.label}</NavLink>)}</nav>
    <div className="side-bottom"><div className="side-note"><span className="small-star">✦</span><strong>让团队知识，持续生长</strong>
      <p>汇集经验，连接信息。<br />让每一份知识发挥价值。</p></div>
      <nav><NavLink to="/settings" className={({ isActive }) => isActive ? 'active' : ''} aria-label="系统设置"><span aria-hidden="true">⚙</span>系统设置</NavLink></nav>
      <div className="profile"><span className="avatar">{me.name.slice(0, 1) || '用'}</span><div><strong>{me.name}</strong><small>已验证身份</small></div>
        <button type="button" onClick={logout} title="退出登录" aria-label="退出登录">↪</button></div>
    </div>
  </aside><div className="main-shell"><header className="topbar"><div className="breadcrumb">工作空间 <span>/</span> <strong>{current}</strong></div>
    <div className="top-meta"><span className="connection">● 已连接工作空间</span><span className="divider" /><span id="today">{new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })}</span></div></header>
    <main id="main" tabIndex={-1}>
      <Routes>
        <Route path="/overview" element={<Overview />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/evaluation" element={<Evaluation />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/chat" element={null} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Routes>
      <div style={{ display: location.pathname === '/chat' ? undefined : 'none' }}><Chat /></div>
    </main><footer>掌柜智库 <span>·</span> 每一份知识，都值得被发现。<span className="footer-right">企业知识工作台</span></footer>
  </div></div>;
}
