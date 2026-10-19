import { purchaseRequest } from './purchase-orders.js'

// The inventory endpoint is readable by warehouse staff as well as purchasers.
// Fetch every page; the visible table's current page is not the material catalog.
export async function allMaterials(signal) {
 const records=new Map()
 for(let current=1; ;current++){
  const data=await purchaseRequest('dashboard/inventory?'+new URLSearchParams({current:String(current),size:'100'}),{signal})
  if(signal?.aborted)throw new DOMException('Aborted','AbortError')
  if(!Array.isArray(data.records)||!Number.isSafeInteger(Number(data.total))||Number(data.total)<0)throw Error('物料列表读取异常，请您重试。')
  const previous=records.size
  for(const part of data.records){if(!Number.isSafeInteger(Number(part.id))||Number(part.id)<=0)throw Error('物料资料不完整，请您核实主数据。');records.set(Number(part.id),part)}
  if(records.size>=Number(data.total))return [...records.values()].sort((a,b)=>Number(a.id)-Number(b.id))
  if(records.size===previous)throw Error('物料列表已变化，请您刷新后重试。')
 }
}
