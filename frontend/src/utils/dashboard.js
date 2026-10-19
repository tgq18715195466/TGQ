export const supplierStatus = {1:'合作中',2:'暂停合作',3:'已终止'}
export const orderStatus = {1:'待审核',2:'已审核',3:'采购中',4:'已入库',5:'已取消'}
export const stockStatus = {out:'缺货',low:'低库存',normal:'正常',unrecorded:'未建库存记录',unconfigured:'未配置阈值'}
export function replenishmentDraft(row) {
 return `请帮我发起补货流程。\n物料：${row.name}（编码 ${row.part_code}，物料ID ${row.id}）\n供应商：${row.supplier_name || '未关联'}（ID ${row.supplier_id ?? '待确认'}）\n当前库存：${row.quantity ?? row.current_quantity ?? '暂无记录'}；库存预警值：${row.stock_warning_value ?? '未配置'}；库存预警值缺口：${row.shortage ?? '待核实'}。\n以上信息来自看板快照，请先重新查询供应商状态、物料归属和未完成采购订单，避免重复采购。缺口仅供参考，不代表我已确认采购数量。请让我补充并确认采购数量、单价和预计交货日期，然后提交人工审批；不要直接下单。`
}
