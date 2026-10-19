export const roleLabels = { purchaser:'采购员', warehouse:'仓管员', approver:'审批人', admin:'管理员' }
const roles = {
 purchaser:['procurement','read_procurement','read_inventory','chat'],
 warehouse:['warehouse','read_procurement','read_inventory','chat'],
 approver:['procurement','warehouse','approve','master','read_procurement','read_inventory','chat'],
 admin:['procurement','warehouse','approve','master','read_procurement','read_inventory','chat','users'],
}
export function hasPermission(user, permission) {
 return !!user && user.enabled !== false && !!roles[user.role]?.includes(permission) && (!Array.isArray(user.permissions) || user.permissions.includes(permission))
}
export function isOwn(user, record) {
 return !!user && ((!!user.actor && record?.actor === user.actor) || (user.id != null && record?.created_by != null && String(record.created_by) === String(user.id)))
}
export function canReview(user, record) { return hasPermission(user,'approve') && !isOwn(user,record) }
export function approvalBlock(user, record) {
 if (!hasPermission(user,'approve')) return '您可以查看待处理内容，请等待审批人审核。'
 if (isOwn(user,record)) return '您不能审批本人提交的申请，请由其他审批人处理。'
 return ''
}
const groups = [
 {key:'home',label:'首页',mark:'首',tabs:[{key:'overview',label:'业务总览'}]},
 {key:'assistant',label:'智能助手',mark:'AI',tabs:[{key:'chat',label:'对话工作台',permission:'chat'}]},
 {key:'procurement',label:'采购管理',mark:'采',tabs:[{key:'orders',label:'采购订单',permission:'read_procurement'},{key:'requests',label:'采购申请',permission:'read_procurement'}]},
 {key:'warehouse',label:'库存管理',mark:'库',tabs:[{key:'inventory',label:'物料库存',permission:'read_inventory'},{key:'receiving',label:'入库收货',permission:'warehouse'},{key:'stock',label:'库存流水',permission:'warehouse'}]},
 {key:'foundation',label:'基础资料',mark:'资',tabs:[{key:'suppliers',label:'供应商',permission:'read_procurement'},{key:'master',label:'主数据维护',permission:'master'}]},
 {key:'account',label:'账户设置',mark:'您',tabs:[{key:'password',label:'修改我的密码'},{key:'users',label:'账号管理',permission:'users'}]},
]
export function navigationFor(user) {
 if (!roles[user?.role]) return []
 return groups.map(g=>({...g,tabs:g.tabs.filter(t=>!t.permission||hasPermission(user,t.permission))})).filter(g=>g.tabs.length)
}
