// A server answer ID owns one bubble for facts, text chunks and the durable answer.
function answerMessage(messages, event) {
  let message = event.id ? messages.find(m => m.id === event.id && m.role === 'assistant') : null
  if (!message) {
    message = {id:event.id || crypto.randomUUID(), role:'assistant', source:'main', channel:'answer', content:'', animate:false}
    messages.push(message)
  }
  return message
}

// Producers should supply result_id. Without it, kind + title identifies a result.
export function mergeBusinessResults(existing, incoming, replace = false) {
  const results = !replace && Array.isArray(existing) ? existing.filter(result => result && typeof result === 'object' && !Array.isArray(result)) : []
  for (const result of Array.isArray(incoming) ? incoming : []) {
    if (!result || typeof result !== 'object' || Array.isArray(result)) continue
    const index = results.findIndex(item => result.result_id != null
      ? item.result_id === result.result_id
      : item.result_id == null && item.kind === result.kind && item.title === result.title)
    if (index < 0) results.push(result)
    else results[index] = result
  }
  return results
}

export function upsertBusinessResult(messages, event) {
  if (!event.id || !event.result || typeof event.result !== 'object' || Array.isArray(event.result)) return null
  const message = answerMessage(messages, event)
  message.business_results = mergeBusinessResults(message.business_results, [event.result])
  message.wasStreamed = true
  // Metadata must not restart or complete the model typewriter.
  return message
}

export function upsertAnswer(messages, event, complete = false) {
  const message = answerMessage(messages, event)
  if (typeof event.content === 'string') {
    message.content = complete ? event.content : message.content + event.content
  }
  if (Array.isArray(event.business_results)) {
    message.business_results = mergeBusinessResults(message.business_results, event.business_results, complete && event.business_results_mode === 'replace')
  }
  if (Array.isArray(event.failure_tool_call_ids)) message.failure_tool_call_ids = [...event.failure_tool_call_ids]
  // A referenced subagent report arrives as one final answer event with no
  // answer_delta. Mark it live now, before Vue can render the full text and
  // before the separate done event reaches the browser.
  message.wasStreamed = true
  message.answerStreaming = !complete
  return message
}

// Public UI copy is Chinese. Transport keys and raw internal status are never copy.
export function businessCopy(value, fallback = '') {
  if (typeof value !== 'string' || !/[\u4e00-\u9fff]/.test(value)) return fallback
  if (/(?:request[_ -]?id|result[_ -]?id|interrupt[_ -]?id|thread[_ -]?id|checkpoint[_ -]?id|internal[_ -]?status|idempotency|幂等|\b(?:part|supplier|order)[_ -]?id\b)/i.test(value)) return fallback
  return value.trim().replace(/你/g, '您')
}

// Execution cards own their failure reason; exact equality preserves unrelated
// explanation and suggestions without interpreting model prose.
export function summaryWithoutDuplicateFailures(content, results) {
  if (typeof content !== 'string') return ''
  const reasons = new Set((Array.isArray(results) ? results : [])
    .filter(result => result?.execution && result.execution.outcome !== 'success')
    .map(result => result.execution.message?.trim()).filter(Boolean))
  return content.split(/\r?\n/).filter(line => !reasons.has(line.trim())).join('\n').replace(/\n{3,}/g, '\n\n').trim()
}
