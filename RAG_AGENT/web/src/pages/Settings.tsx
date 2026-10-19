import { Button, PageHead } from '../components';
import { useWorkspace } from '../workspace';

export default function Settings() {
  const { me, status, refresh, onError, logout } = useWorkspace();
  const account = [
    ['成员', me.name], ['用户标识', me.id], ['租户标识', me.tenant_id],
    ['API 地址', location.origin], ['凭证存储', '当前浏览器会话'],
  ];
  const services = [
    ['应用接口', status.api === 'ok' ? '可访问' : '未确认'],
    ['数据库', status.postgres === 'connected' ? '已连接' : '未连接'],
    ['运行环境', status.environment || '未报告'],
    ['文件扫描', status.scanner_configured ? '已配置，未验证连通性' : '未配置'],
    ['联网搜索', status.web_search_enabled ? '已启用' : '未启用'],
    ['外部服务', status.external_services_verified ? '已验证' : '尚未验证连通性'],
  ];
  return <><PageHead title="系统设置" description="查看工作空间连接与服务配置状态。">
    <Button onClick={() => refresh().catch(onError)}>刷新状态</Button></PageHead>
    <div className="settings-grid"><section className="panel padded"><div className="section-head"><div><h2>当前工作空间</h2><p>已验证身份与当前连接</p></div></div>
      {account.map(([label, value]) => <div className="status-row" key={label}><span>{label}</span><span>{value}</span></div>)}
      <Button className="subtle" onClick={logout}>退出并切换凭证</Button></section>
      <section className="panel padded"><div className="section-head"><div><h2>服务状态</h2><p>状态来自当前后端响应</p></div></div>
      {services.map(([label, value]) => <div className="status-row" key={label}><span>{label}</span><span>{value}</span></div>)}</section></div>
    <p className="notice">{status.worker_note || '后台任务依赖工作进程。API 可访问不代表模型、解析或索引服务已经就绪。'}</p>
  </>;
}
