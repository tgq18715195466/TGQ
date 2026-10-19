export const labels={supplier_code:'供应商编号',part_code:'物料编号',name:'名称',status:'合作状态',supplier_id:'供应商ID',purchase_price:'采购单价',stock_warning_value:'库存预警值',enabled:'启用',contact_person:'联系人',phone:'电话',email:'邮箱',address:'地址',credit_rating:'信用等级',model:'型号',unit:'单位',category:'分类',specification:'规格',id:'ID'}
export const states={applied:'已生效',pending:'待审批',approved:'已通过',rejected:'已驳回',ordered:'已下单',partial:'部分到货',complete:'全部到货',receipt:'采购入库',issue:'领料出库',return:'退库',count:'盘点调整',adjust:'Agent库存调整',supplier:'供应商',part:'物料'}
export function display(value,key){if(value==null)return '—';if(key==='status'&&typeof value==='number')return ({1:'待审核',2:'已审核',3:'采购中',4:'已入库',5:'已取消'})[value]||value;if(key==='enabled')return value?'启用':'停用';return states[value]||value}
export function changeRows(previous,proposed){return Object.entries(proposed||{}).map(([key,value])=>({id:key,field:labels[key]||key,before:format(previous?.[key],key),after:format(value,key)}))}
function format(v,k){if(k==='status')return ({1:'合作中',2:'暂停合作',3:'已终止'})[v]||'—';return display(v,k)}
export function receiptPayload(lines){return lines.map(x=>({detail_id:x.id,accepted:Number(x.receive||0),rejected:Number(x.reject||0)})).filter(x=>x.accepted+x.rejected>0)}
export function masterValues(entity,form){
 const keys=entity==='supplier'?['supplier_code','name','status','credit_rating','contact_person','phone','email','address']:['part_code','name','supplier_id','purchase_price','stock_warning_value','enabled','model','unit','category','specification']
 const result=Object.fromEntries(keys.filter(k=>k in form).map(k=>[k,form[k]]))
 for(const key of ['supplier_id','purchase_price','stock_warning_value','status'])if(key in result)result[key]=Number(result[key])
 return result
}
export function operationKey(cryptoApi=globalThis.crypto){
 if(cryptoApi?.randomUUID)return cryptoApi.randomUUID()
 if(!cryptoApi?.getRandomValues)throw Error('浏览器不支持安全请求编号，请使用本机地址或 HTTPS')
 return Array.from(cryptoApi.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')
}
export function businessError(detail){
 if(typeof detail==='string')return detail
 if(Array.isArray(detail))return detail.map(item=>item.msg||'字段填写不正确').join('；')
 if(detail?.code==='SUPPLIER_NOT_ACTIVE'){
  const state=({1:'合作中',2:'暂停合作',3:'已终止'})[detail.status]||'非合作状态'
  return `供应商 #${detail.supplier_id ?? '未知'} 当前为“${state}”，该订单不能审核通过。若确需恢复合作，请在“基础资料 → 主数据维护”提交状态变更，经审批生效后再审核订单；也可取消原订单，改向合格供应商重新申请。`
 }
 const codes={SUPPLIER_NOT_FOUND:'供应商不存在或已删除，请核实供应商资料。',PART_NOT_FOUND:'订单中的物料不存在或已删除，请核实明细。',SUPPLIER_PART_MISMATCH:'订单物料与供应商归属不一致，请核实后重新申请。',ORDER_CONFLICT:'订单数据冲突，请刷新后重试。'}
 return codes[detail?.code]||'操作未完成，请检查填写内容并刷新后重试。'
}
