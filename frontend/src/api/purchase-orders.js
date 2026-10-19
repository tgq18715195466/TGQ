import { apiFetch, friendlyError } from './http.js'
import { businessError } from '../utils/operations.js'

// These endpoints mix legacy {code,data} envelopes and plain operation records.
export async function purchaseRequest(path, { body, signal } = {}) {
 const response = await apiFetch('/api/' + path, {
  signal,
  ...(body ? { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) } : {}),
 })
 let data
 try { data = await response.json() }
 catch (error) { if (error.name === 'AbortError') throw error; throw new Error('服务响应异常，请您稍后重试。') }
 if (!response.ok) {
  const detail = data.detail
  if(path === 'orders/create' && (detail?.code === 'ORDER_CONFLICT' || (response.status === 409 && typeof detail === 'string' && /重复|已存在|duplicate/i.test(detail)))) {
   throw new Error(`订单编号 ${body.orderNumber} 可能已提交或存在冲突，请您先在采购订单列表核查，勿重复创建。`)
  }
  const message = detail && typeof detail === 'object' && !Array.isArray(detail)
   ? businessError(detail)
   : friendlyError(detail, '提交信息不符合要求，请您核对供应商、物料、数量和交货日期。')
  throw new Error(message)
 }
 if (data.code != null && ![0,200,201].includes(Number(data.code))) {
  throw new Error(friendlyError(data.message || data.msg))
 }
 return data.data ?? data
}
