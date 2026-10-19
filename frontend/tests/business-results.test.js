import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { createSSRApp, reactive, nextTick } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { component, mount, settle } from './component-helper.js'
import * as answers from '../src/utils/answer-stream.js'
import { streamChat, resumeChat } from '../src/api/chat.js'
import {setUser} from '../src/auth/state.js'
test.beforeEach(()=>setUser({id:1,username:'buyer',role:'purchaser'}))

const result = (overrides = {}) => ({
  result_id: 'r1', title: '库存查询结果', kind: 'inventory',
  records: [{ name: '物料甲', quantity: 20, stock_state: 'low', unit_price: '12.50' }],
  total: 1, returned_count: 1, complete: true,
  columns: [{key:'name',label:'物料名称'}, {key:'quantity',label:'库存数量',format:'number'}, {key:'stock_state',label:'库存状态',format:'state'}, {key:'unit_price',label:'单价',format:'money'}],
  execution: {outcome:'success',message:'已核实库存。'}, ...overrides,
})
const backendFixture = JSON.parse(readFileSync(new URL('./fixtures/business-results-backend.json', import.meta.url), 'utf8'))
const send = (controller, event) => {
  const bytes = new TextEncoder().encode('data: ' + JSON.stringify(event) + '\r\n\r\n')
  for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3))
}

test('fixed procurement supplement requires typed fields and has no free-text escape',async()=>{
  const Component=await component('components/InterruptBanner.vue',true)
  const html=await renderToString(createSSRApp(Component,{interruptData:{
    interrupt_type:'order_info_supplement',interrupt_id:'fixed-form',workflow_type:'procurement',
    fields:[{path:'0.reason',name:'reason',label:'补货原因',type:'text',required:true,order_index:0}],
    orders:[{}],missing_fields:'补货原因：必填'},submitting:false}))
  assert.doesNotMatch(html,/信息尚不确定，改用自由补充/)
  assert.match(html,/请在表单中补齐必填字段/)
})

test('business result owns the answer bubble before text and upserts by result_id', () => {
  assert.equal(typeof answers.upsertBusinessResult, 'function')
  const messages = [{id:'u',role:'user',content:'查询库存'}]
  const first = answers.upsertBusinessResult(messages,{id:'a',result:result()})
  assert.equal(first.content,''); assert.equal(first.role,'assistant'); assert.equal(first.channel,'answer')
  assert.equal(first.business_results.length,1)
  answers.upsertBusinessResult(messages,{id:'a',result:result({records:[{name:'物料乙',quantity:0}]})})
  answers.upsertBusinessResult(messages,{id:'a',result:result({result_id:'r2',title:'订单查询结果'})})
  assert.equal(first.business_results.length,2)
  assert.equal(first.business_results[0].records[0].name,'物料乙')
  answers.upsertAnswer(messages,{id:'a',content:'请您核对。'},false)
  answers.upsertAnswer(messages,{id:'a',content:'请您核对。'},true)
  assert.equal(messages.length,2); assert.equal(messages[1],first)
  assert.equal(first.business_results.length,2); assert.equal(first.wasStreamed,true)
})

test('answer restores and merges metadata without resetting content on result-only final', () => {
  const messages=[]
  answers.upsertAnswer(messages,{id:'a',content:'分析',business_results:[result()]},false)
  assert.equal(messages[0].business_results?.length,1)
  answers.upsertAnswer(messages,{id:'a',business_results:[result({result_id:'r2'})]},true)
  assert.equal(messages[0].content,'分析')
  assert.equal(messages[0].business_results.length,2)
  answers.upsertAnswer(messages,{id:'a',content:'',business_results:[]},true)
  assert.equal(messages[0].content,''); assert.equal(messages[0].business_results.length,2)
})

test('result merging tolerates invalid legacy entries and ignores malformed incoming values',()=>{
  const messages=[{id:'a',role:'assistant',content:'分析',business_results:[null,'invalid']}]
  assert.doesNotThrow(()=>answers.upsertAnswer(messages,{id:'a',business_results:[null,result()]},true))
  assert.equal(messages[0].business_results.length,1)
})

test('actual SSE parser delivers business results immediately for stream and resume, and restores done metadata',async()=>{
  const old=globalThis.fetch
  try {
    for (const resume of [false,true]) {
      let controller; const messages=[], seen=[]
      globalThis.fetch=async()=>new Response(new ReadableStream({start(value){controller=value}}))
      const callbacks={onBusinessResult:event=>{seen.push(event); answers.upsertBusinessResult(messages,event)},onAnswer:event=>answers.upsertAnswer(messages,event,true)}
      const pending=resume?resumeChat('s',{interrupt_id:'i',workflow_action:'retry'},callbacks):streamChat('查询','s',callbacks)
      await settle()
      send(controller,{type:'business_result',id:'a',result:result()}); await settle()
      assert.equal(seen.length,1); assert.equal(messages[0].content,''); assert.equal(messages[0].business_results.length,1)
      send(controller,{type:'answer',id:'a',content:'请您核对。',business_results:[result()]})
      send(controller,{type:'done',id:'a',business_results:[result({result_id:'r2'})]}); controller.close()
      const final=await pending
      assert.equal(final.id,'a'); assert.equal(final.business_results.length,2)
      assert.equal(final.content,'请您核对。'); assert.equal(messages.length,1)
    }
  } finally {globalThis.fetch=old}
})

test('done alone preserves explicit empty content and delivers restored results',async()=>{
  const old=globalThis.fetch
  globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){send(controller,{type:'answer_delta',id:'a',content:'旧正文'});send(controller,{type:'done',id:'a',content:'',business_results:[result()]});controller.close()}}))
  try {const final=await streamChat('查询','s'); assert.equal(final.content,'');assert.equal(final.business_results?.length,1)} finally {globalThis.fetch=old}
})

test('actual SSR result table escapes cells and hides unknown fields and unsafe columns',async()=>{
  assert.ok(existsSync(new URL('../src/components/BusinessResult.vue',import.meta.url)),'BusinessResult.vue must exist')
  const Component=await component('components/BusinessResult.vue',true)
  const html=await renderToString(createSSRApp(Component,{result:result({
    request_id:'secret-request',internal_status:'secret-state',
    records:[{name:'<img src=x onerror=alert(1)>',quantity:0,stock_state:'low',unit_price:'9007199254740993.25',id:'secret-id',request_id:'secret-request',hidden:'secret-extra'}],
    columns:[...result().columns,{key:'id',label:'内部编号'},{key:'request_id',label:'请求编号'},{key:'hidden',label:'附加字段'},{key:'supplier_name',label:'supplier_id',format:'html'}],
  })}))
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|onerror="|secret-|内部编号|请求编号|附加字段|supplier_id/)
  assert.match(html,/9,007,199,254,740,993\.25/);assert.match(html,/低库存/);assert.match(html,/>0</)
  assert.doesNotMatch(readFileSync(new URL('../src/components/BusinessResult.vue',import.meta.url),'utf8'),/v-html/)
})

test('backend JSON fixture renders every public schema column and hides injected tool and ID fields',async()=>{
  const Component = await component('components/BusinessResult.vue', true)
  const escapeText = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  assert.equal(backendFixture.results.length, 10)
  const keys = new Set()
  for (const publicResult of backendFixture.results) {
    const hidden = {id:'secret-id', part_id:'secret-part', supplier_id:'secret-supplier', request_id:'secret-request', source_tool:'secret-tool', tool_name:'secret-name', internal_status:'secret-state'}
    const payload = {...publicResult, ...hidden, records:publicResult.records.map(row => ({...row, ...hidden})),
      columns:[...publicResult.columns, ...Object.keys(hidden).map(key => ({key, label:'内部字段', format:'text'}))]}
    const html = await renderToString(createSSRApp(Component, {result:payload}))
    assert.equal((html.match(/<th(?=[\s>])/g) || []).length, publicResult.columns.length, publicResult.kind + ' must retain every public column')
    for (const column of publicResult.columns) {
      keys.add(column.key)
      assert.ok(html.includes('>' + column.label + '</th>'), publicResult.kind + '.' + column.key + ' header missing')
      assert.ok(html.includes('>' + escapeText(publicResult.records[0][column.key]) + '</td>'), publicResult.kind + '.' + column.key + ' value missing')
    }
    assert.doesNotMatch(html, /secret-|内部字段|source_tool|request_id|tool_name|internal_status/)
    assert.doesNotMatch(html, /2026\\-|&amp;amp;/)
  }
  assert.equal(keys.size, 40, 'fixture must cover all distinct keys from backend _SCHEMAS')
})

test('backend Chinese status labels accept explicit text format and ISO dates stay plaintext',async()=>{
  const Component = await component('components/BusinessResult.vue', true)
  const request = backendFixture.results.find(result => result.kind === 'purchase_request')
  const replenishment = backendFixture.results.find(result => result.kind === 'replenishment')
  const requestHtml = await renderToString(createSSRApp(Component, {result:{...request,
    columns:request.columns.map(column => column.key === 'status_name' ? {...column, format:'text'} : column)}}))
  assert.match(requestHtml, /申请状态/); assert.match(requestHtml, /待审批/)
  assert.match(requestHtml, /预计交货日期/); assert.match(requestHtml, /2026-10-09/)
  const stockHtml = await renderToString(createSSRApp(Component, {result:{...replenishment,
    columns:replenishment.columns.map(column => ['supplier_status_name', 'state_label'].includes(column.key) ? {...column, format:'text'} : column)}}))
  assert.match(stockHtml, /供应商状态/); assert.match(stockHtml, /合作中/)
  assert.match(stockHtml, /启用状态/); assert.match(stockHtml, />启用</)
  assert.match(stockHtml, /上海供应商（总部） &amp; 配件/)
  assert.doesNotMatch(stockHtml, /&amp;amp;|未知状态/)
})

test('actual SSR renders every row and exact incomplete counts without claiming all',async()=>{
  assert.ok(existsSync(new URL('../src/components/BusinessResult.vue',import.meta.url)))
  const Component=await component('components/BusinessResult.vue',true)
  const records=Array.from({length:125},(_,i)=>({name:'物料'+i,quantity:i}))
  const html=await renderToString(createSSRApp(Component,{result:result({records,total:300,returned_count:125,complete:false})}))
  assert.equal((html.match(/<tr/g)||[]).length,126);assert.match(html,/本次125条\/共300条/);assert.doesNotMatch(html,/全部|所有/)
  const complete=await renderToString(createSSRApp(Component,{result:result({records,total:125,returned_count:125,complete:true})}))
  assert.match(complete,/已返回125条\/共125条/)
  const mismatch=await renderToString(createSSRApp(Component,{result:result({records,total:300,returned_count:300,complete:true})}))
  assert.match(mismatch,/本次125条\/共300条/)
})

test('unknown state and malformed payload never show raw state, objects or English UI labels',async()=>{
  assert.ok(existsSync(new URL('../src/components/BusinessResult.vue',import.meta.url)))
  const Component=await component('components/BusinessResult.vue',true)
  const html=await renderToString(createSSRApp(Component,{result:result({title:'query_tool',execution:{outcome:'secret-status',message:'request_id: secret-request'},records:[{name:{id:'secret-id'},quantity:NaN,stock_state:'internal_pending',unit_price:'invalid'}]})}))
  assert.match(html,/业务结果|执行状态待确认/);assert.match(html,/未知状态/)
  assert.doesNotMatch(html,/query_tool|secret-|internal_pending|\[object Object\]|NaN|invalid/)
})

test('MessageItem keeps evidence collapsed and retains typed text on metadata updates',async()=>{
  const Component=await component('components/MessageItem.vue')
  const message=reactive({id:'a',role:'assistant',channel:'answer',content:'',business_results:[result()],wasStreamed:true,answerStreaming:true})
  const app=mount(Component,{message,isStreaming:true})
  try {
    assert.match(app.text(),/查看查询依据/);assert.doesNotMatch(app.text(),/物料甲|分析说明/)
    message.content='请您核对库存。';await nextTick();await new Promise(resolve=>setTimeout(resolve,55));await nextTick()
    assert.doesNotMatch(app.text(),/分析说明/)
    const modelHtml=()=>app.nodes().find(n=>n.props?.class==='markdown-renderer')?.props.innerHTML
    const before=modelHtml();assert.match(before, /请您/)
    message.business_results.push(result({result_id:'r2'}));await nextTick()
    assert.equal(modelHtml(),before)
    message.answerStreaming=false; await nextTick()
    assert.equal(modelHtml(),before);assert.doesNotMatch(app.text(),/分析说明/)
  } finally {app.unmount()}
})

test('SSR saved assistant message puts answer before closed evidence',async()=>{
  const Component=await component('components/MessageItem.vue',true)
  const html=await renderToString(createSSRApp(Component,{message:{id:'a',role:'assistant',content:'请您核对库存。',business_results:[result()]}}))
  assert.doesNotMatch(html,/物料甲|分析说明/);assert.match(html,/查看查询依据/)
  assert.ok(html.indexOf('answer-body')<html.indexOf('business-evidence'))
})

test('workflow recovery renders Chinese copy and emits only explicit bound retry/cancel',async()=>{
  const Component=await component('components/InterruptBanner.vue'), emitted=[]
  const old=globalThis.fetch;globalThis.fetch=()=>assert.fail('recovery must not query or retry on mount')
  const app=mount(Component,{interruptData:{interrupt_type:'workflow_recovery',interrupt_id:'i1',workflow_type:'replenishment',title:'库存核验未完成',message:'请您重新核验库存。',request_id:'secret'},onResume:value=>emitted.push(value)})
  try {
    await settle();assert.match(app.text(),/库存核验未完成/);assert.doesNotMatch(app.text(),/secret|workflow_recovery|replenishment/);assert.equal(emitted.length,0)
    await app.click('重新核验');await app.click('取消本次操作')
    assert.deepEqual(emitted,[{interrupt_id:'i1',workflow_action:'retry'},{interrupt_id:'i1',workflow_action:'cancel'}])
  } finally {app.unmount();globalThis.fetch=old}
})

test('workflow recovery submitting locks both actions',async()=>{
  const Component=await component('components/InterruptBanner.vue'),emitted=[]
  const app=mount(Component,{interruptData:{interrupt_type:'workflow_recovery',interrupt_id:'i'},submitting:true,onResume:value=>emitted.push(value)})
  try {assert.equal(app.button('重新核验')?.props.disabled,true);await app.click('重新核验');await app.click('取消本次操作');assert.deepEqual(emitted,[])} finally {app.unmount()}
})

test('workflow recovery accepts the Chinese result_message without exposing metadata',async()=>{
  const Component=await component('components/InterruptBanner.vue',true)
  const html=await renderToString(createSSRApp(Component,{interruptData:{interrupt_type:'workflow_recovery',interrupt_id:'secret-id',result_message:'库存结果暂未核实，请您重新核验。',internal_status:'secret-state'}}))
  assert.match(html,/库存结果暂未核实/);assert.doesNotMatch(html,/secret-|internal_status/)
})

test('workflow supplement preserves workflow_type with validated values and reason',async()=>{
  const Component=await component('components/InterruptBanner.vue'),emitted=[]
  const app=mount(Component,{interruptData:{interrupt_type:'order_info_supplement',interrupt_id:'i',workflow_type:'replenishment',reason:'请您确认采购数量。',fields:[{path:'orders.0.quantity',order_index:0,label:'采购数量',type:'integer',required:true,min:1,value:3}]},onResume:value=>emitted.push(value)})
  try {assert.match(app.text(),/请您确认采购数量/);await app.click('提交补充信息');assert.deepEqual(emitted,[{interrupt_id:'i',workflow_type:'replenishment',values:{'orders.0.quantity':'3'},supplement:''}])} finally {app.unmount()}
})
