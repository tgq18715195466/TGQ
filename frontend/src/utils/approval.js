import {hasPermission} from '../auth/permissions.js'

const actionPermissions={order_create:'procurement',order_update:'procurement',purchase_request_create:'procurement',inventory_adjust:'warehouse',master_change_request:'master'}
export function canHandleInterrupt(user,item){
 if(['order_info_supplement','workflow_recovery'].includes(item?.interrupt_type))return hasPermission(user,'procurement')
 if(item?.interrupt_type==='hitl_approval'){
  const actions=item.action_requests||[]
  return actions.length>0 && actions.every(action=>hasPermission(user,actionPermissions[action.name]))
 }
 return false
}
export function interruptPermissionMessage(item){
 const purchase=['order_info_supplement','workflow_recovery'].includes(item?.interrupt_type)||(item?.action_requests||[]).some(action=>actionPermissions[action.name]==='procurement')
 return purchase?'您的账号没有采购权限，采购申请、下单及订单修改请由采购员处理。您可以继续办理收货和库存作业。':'您的账号没有执行该操作的权限，请联系相应业务人员处理。'
}

export function approvalResponse(interrupt, choices) {
  const actions = interrupt.action_requests || []
  if (!actions.length || choices.length !== actions.length || choices.some(x => !['approve', 'reject'].includes(x))) {
    throw new Error('请为每个操作选择同意或拒绝')
  }
  return { interrupt_id: interrupt.interrupt_id, decisions: choices.map(type => ({type})) }
}
export function pendingFromHistory(response) {
  const pending = response.interrupts || []
  return pending.length ? { ...pending[0], thread_id: response.thread_id, interrupts: pending } : null
}
