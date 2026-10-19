import {purchaseRequest} from './purchase-orders.js'

async function pages(path,signal){
 const rows=new Map()
 for(let current=1;;current++){
  const data=await purchaseRequest(path+(path.includes('?')?'&':'?')+new URLSearchParams({current:String(current),size:'100'}),{signal})
  if(signal.aborted)throw new DOMException('Aborted','AbortError')
  if(!Array.isArray(data.records)||!Number.isSafeInteger(Number(data.total))||Number(data.total)<0)throw Error('采购资料读取异常，请您重试。')
  const before=rows.size
  for(const row of data.records){if(!Number.isSafeInteger(Number(row.id))||Number(row.id)<1)throw Error('采购资料不完整，请您核实主数据。');rows.set(Number(row.id),row)}
  if(rows.size>=Number(data.total))return [...rows.values()].sort((a,b)=>Number(a.id)-Number(b.id))
  if(rows.size===before)throw Error('采购资料已变化，请您重新加载。')
 }
}
export async function procurementOptions(signal){
 const [suppliers,parts]=await Promise.all([pages('dashboard/suppliers?status=1',signal),pages('parts/page',signal)])
 const active=suppliers.filter(row=>Number(row.status)===1)
 const allowed=new Set(active.map(row=>Number(row.id)))
 return {suppliers:active,parts:parts.filter(row=>row.enabled!==false&&row.enabled!==0&&allowed.has(Number(row.supplier_id)))}
}
