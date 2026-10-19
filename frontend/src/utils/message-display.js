import { publicText, publicToolText, isInternalProbe } from './public-display.js'
// Localize workflow narration at render time, including incomplete SSE chunks.
// Never mutate stored messages or translate identifiers / business results.
export function localizeWorkflowProgress(content) {
  if (typeof content !== 'string' || !content.trim()) return content
  let rest = content.trim().replace(/[’‘]/g, "'")
  const starts = ["I'll start by reading", "I will start by reading", "I'll read your preferences", "I will read your preferences", "I'll delegate", "I will delegate", "I have your preferences", "Now let me delegate", "Let me delegate"]
  const patterns = [
    /^I(?:'ll| will) start by reading\b[^.!?\n]*(?:[.!?]|$)/i,
    /^I(?:'ll| will) read your preferences\b[^.!?\n]*(?:[.!?]|$)/i,
    /^(?:I(?:'ll| will)|(?:Now )?let me) delegate\b[^.!?\n]*(?:[.!?]|$)/i,
    /^I have your preferences(?:[.!?]|$)/i,
  ]
  let recognized = false
  while (rest) {
    if (starts.some(start => start.toLowerCase().startsWith(rest.toLowerCase()))) {
      return '正在准备处理您的请求，请稍候。'
    }
    const match = patterns.map(pattern => rest.match(pattern)).find(Boolean)
    if (!match) break
    recognized = true
    rest = rest.slice(match[0].length).trimStart()
  }
  if (!recognized) return content
  return '正在准备处理您的请求，请稍候。' + (rest ? '\n\n' + rest : '')
}

export function sameAssistantSource(message, source) {
  return message?.role === 'assistant' && (message.source || 'main') === (source || 'main')
}

const writeTools = new Set(['purchase_request_create','order_create','order_update','inventory_adjust','master_change_request'])
const queryTools = new Set(['supplier_query','part_query','part_search','part_by_supplier','inventory_warning','order_search_details','replenishment_suggestions','procurement_verification'])
const queryKinds = new Set(['supplier','part','inventory','replenishment','order_detail','procurement'])
const writeKinds = new Set(['purchase_request','order','inventory_adjustment','master_change'])

// Filter the procurement turn, keeping earlier readonly turns and tool records.
export function procurementDisplayMessages(messages, pending = []) {
  const output = []
  let start = 0
  while (start < messages.length) {
    let end = start + 1
    while (end < messages.length && messages[end].role !== 'user') end++
    const turn = messages.slice(start,end)
    const names = new Map(turn.filter(m=>m.role==='tool').map(m=>[m.id,m.tool_name]))
    const writes = turn.some(m=>writeTools.has(m.tool_name) || m.business_results?.some(r=>writeKinds.has(r?.kind)))
      || end === messages.length && pending.some(item=>item.workflow_type==='procurement' || item.action_requests?.some(a=>writeTools.has(a.name)))
    for (const message of turn) {
      if (!writes || !Array.isArray(message.business_results)) { output.push(message); continue }
      output.push({...message,business_results:message.business_results.filter(result=>{
        const name = names.get(result?.result_id)
        return !queryTools.has(name) && !(name == null && queryKinds.has(result?.kind))
      })})
    }
    start = end
  }
  return output
}

function failureIsRepresented(message, turn) {
  return turn.some(answer=>{
    if (answer.role !== 'assistant' || (answer.channel && answer.channel !== 'answer')) return false
    if (answer.business_results?.some(result=>result.result_id===message.id && ['failure','unknown'].includes(result.execution?.outcome))) return true
    if (!answer.content?.trim()) return false
    if (Array.isArray(answer.failure_tool_call_ids)) return answer.failure_tool_call_ids.includes(message.id)
    if (message.failure?.answer_id === answer.id) return true
    // Legacy records: match the specific reason, never an unrelated later answer.
    return typeof message.failure?.message==='string' && answer.content.includes(message.failure.message)
  })
}

export function buildDisplayItems(messages, showTools) {
  const items = []
  const visible = procurementDisplayMessages(messages)
  for (const [index, raw] of visible.entries()) {
    const original = raw.role==='assistant' ? {...raw,content:publicText(raw.content)} : raw.role==='tool' ? {...raw,args:publicToolText(raw.args || ''),text:publicToolText(raw.text || ''),failure:isInternalProbe(raw.tool_name)?null:raw.failure} : raw
    if (original.channel === 'internal' || original.channel === 'progress') continue
    if (original.role==='assistant' && !original.content?.trim() && Array.isArray(original.business_results) && !original.business_results.length) continue
    let start = index
    while (start>0 && visible[start].role!=='user') start--
    let end = index+1
    while (end<visible.length && visible[end].role!=='user') end++
    if (original.failure && !failureIsRepresented(original,visible.slice(start,end))) items.push({kind:'failure', key:`failure-${original.id || index}`, message:original})
    const message = original.role === 'assistant' && original.channel !== 'answer' ? {...original, content: localizeWorkflowProgress(original.content)} : original
    const internal = message.role === 'tool' || (message.role === 'assistant' && message.source && message.source !== 'main')
    if (message.role === 'tool' && !showTools) continue
    if (internal) {
      let group = items.at(-1)
      if (group?.kind !== 'details') {
        group = { kind: 'details', key: `details-${message.id || index}`, messages: [] }
        items.push(group)
      }
      group.messages.push(message)
    } else {
      items.push({ kind: 'message', key: message.id || `message-${index}`, message })
    }
  }
  return items
}
