import {publicData,publicText} from './public-display.js'
const aliases = { supplier_id:'supplierId', supplier_name:'supplierName', order_detail:'orderDetail', order_id:'orderId', order_number:'orderNumber', total_amount:'totalAmount', expected_delivery_date:'expectedDeliveryDate', actual_delivery_date:'actualDeliveryDate', order_time:'orderTime', created_by:'createdBy', part_id:'partId', unit_price:'unitPrice', request_id:'requestId', expected_date:'expectedDeliveryDate' }
export const canonicalField = key => aliases[key] || key
const labels={supplierId:'供应商编号（ID）',supplierName:'供应商名称',orderDetail:'订单明细',orderId:'订单ID',orderNumber:'订单编号',totalAmount:'总金额',expectedDeliveryDate:'预计交货日期',actualDeliveryDate:'实际交货日期',orderTime:'下单时间',createdBy:'创建人ID',partId:'物料ID',quantity:'数量',unitPrice:'单价',subtotal:'小计',remark:'备注',status:'状态',delta:'调整数量',reason:'调整原因',requestId:'操作幂等号',manual:'采购方式',demand:'额外需求量'}
export const formatFieldName = key => labels[canonicalField(key)] || key
export function orderDetails(args={}) { return (args.order_detail ?? args.orderDetail ?? []).map(item => Object.fromEntries(Object.entries(item).map(([k,v])=>[canonicalField(k),v]))) }
export function displayArgs(args={}) { return Object.fromEntries(Object.entries(publicData(args)).filter(([k])=>canonicalField(k)!=='orderDetail')) }
export function formatFieldValue(key,value) {
 if(value==null) return '-'
 if(key==='manual')return value?'主动补货':'按系统建议补货'
 if(canonicalField(key)==='status') return ({1:'待审核',2:'已审核',3:'采购中',4:'已入库',5:'已取消'})[value] || String(value)
 return typeof value==='object' ? JSON.stringify(value,null,2) : String(value)
}
export function detailSubtotal(item) { return item.subtotal ?? (Number.isFinite(Number(item.quantity)) && Number.isFinite(Number(item.unitPrice)) && item.quantity!=null && item.unitPrice!=null ? (Number(item.quantity)*Number(item.unitPrice)).toFixed(2) : '-') }
export function translateFields(text='') { return String(text).replace(/\b[a-zA-Z_][a-zA-Z_0-9]*\b/g, key=>formatFieldName(key)).replaceAll('Field required','必填').replaceAll('Input should be','请输入') }
export function formatCollectedData(data) {
 try { const obj=typeof data==='string'?JSON.parse(data):data; return translateFields(JSON.stringify(publicData(obj),null,2)) } catch { return translateFields(publicText(data)) }
}

export function purchaseConfirmationRows(args={},part=null){
 const price=Number(args.unit_price),quantity=Number(args.quantity)
 const money=n=>Number.isFinite(n)?'¥'+n.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'待补充'
 return [
  ['商品名称',part?.name || '待核实'],['物料编号',part?.part_code || '待核实'],
  ['供应商',part?.supplier_name || '待核实'],['采购数量',args.quantity ?? '待补充'],
  ['采购单价',args.unit_price!=null?money(price):'待补充'],
  ['采购金额',args.unit_price!=null&&args.quantity!=null?money(price*quantity):'待补充'],
  ['预计交货日期',args.expected_date || '待补充'],['补货原因',args.reason || '待补充'],
  ['采购方式',args.manual===false?'按系统建议补货':'主动补货']
 ]
}
