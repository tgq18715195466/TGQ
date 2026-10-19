import test from 'node:test'
import assert from 'node:assert/strict'
import { approvalResponse, pendingFromHistory } from '../src/utils/approval.js'
import { streamChat } from '../src/api/chat.js'
const item = {interrupt_id:'pending-1', action_requests:[{name:'order_create'}, {name:'inventory_adjust'}]}
test('one explicit decision per action with stable interrupt identity', () => {
  assert.deepEqual(approvalResponse(item, ['approve','reject']), {interrupt_id:'pending-1', decisions:[{type:'approve'},{type:'reject'}]})
  assert.throws(() => approvalResponse(item, ['approve']))
})
test('reload reconstructs all pending interrupts', () => {
  const restored = pendingFromHistory({thread_id:'t1', interrupts:[item, {...item,interrupt_id:'pending-2'}]})
  assert.equal(restored.thread_id,'t1')
  assert.equal(restored.interrupts.length,2)
  assert.equal(pendingFromHistory({interrupts:[]}),null)
})
test('SSE waits for done after interrupt instead of abandoning stream', async () => {
  const original=globalThis.fetch
  globalThis.fetch=async () => new Response('data: {"type":"interrupt","thread_id":"t1","interrupts":[]}\n\ndata: {"type":"done","thread_id":"t1","interrupted":true}\n\n')
  let completed=false
  try {
    const r=await streamChat('synthetic','t1',{onDone:()=>{completed=true}})
    assert.equal(completed,true)
    assert.equal(r.interrupted,true)
  } finally { globalThis.fetch=original }
})
test('server error is surfaced rather than swallowed as JSON parse error', async () => {
  const original=globalThis.fetch
  globalThis.fetch=async () => new Response('data: {"type":"error","message":"checkpoint unavailable"}\n\n')
  try { await assert.rejects(streamChat('synthetic','t1'), /checkpoint unavailable/) }
  finally { globalThis.fetch=original }
})
