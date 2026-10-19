import test from 'node:test'
import assert from 'node:assert/strict'
import {component,mount,settle} from './component-helper.js'
import {streamChat} from '../src/api/chat.js'
import {setUser} from '../src/auth/state.js'

const MessageItem=await component('components/MessageItem.vue')
const failed={result_id:'write',title:'采购申请',kind:'purchase_request',records:[],columns:[],execution:{outcome:'failure',message:'该物料已有待审批申请，请先处理原申请。'}}
const rendered=app=>app.text()+app.nodes().map(n=>n.props?.innerHTML||'').join('')

test('failure card and unified answer show the reason once while retaining suggestions',async()=>{
 const app=mount(MessageItem,{message:{id:'a',role:'assistant',channel:'answer',content:failed.execution.message+'\n\n建议核查已有申请。',business_results:[failed]}})
 try{
  await settle()
  const text=rendered(app)
  assert.equal(text.split(failed.execution.message).length-1,1)
  assert.match(text,/建议核查已有申请/)
 }finally{app.unmount()}
})

test('SSE complete facts snapshot can clear cached queries and carries failure IDs',async()=>{
 const events=[{type:'business_result',id:'a',result:{result_id:'query',kind:'part',title:'物料查询',records:[],execution:{outcome:'success',message:'查询成功'}}},
  {type:'answer',id:'a',business_results_mode:'replace',content:'操作未完成。',business_results:[failed],failure_tool_call_ids:['write']},
  {type:'done',id:'a',business_results_mode:'replace',content:'操作未完成。',business_results:[],failure_tool_call_ids:['write']}]
 const before=globalThis.fetch
 globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')));controller.close()}}))
 try{
  const result=await streamChat('采购','s',{})
  assert.deepEqual(result.business_results,[])
  assert.deepEqual(result.failure_tool_call_ids,['write'])
 }finally{globalThis.fetch=before}
})

const App=await component('App.vue')
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}})
test('actual App clears preflight card when write begins and renders a failure once',async()=>{
 setUser({id:7,username:'buyer',display_name:'采购员',role:'purchaser'})
 let controller
 const before=globalThis.fetch
 globalThis.fetch=async(url)=>{
  if(url==='/api/chat/stream')return new Response(new ReadableStream({start(c){controller=c}}))
  if(url.startsWith('/api/history'))return response({sessions:[],messages:[],interrupts:[]})
  if(url.includes('/summary'))return response({suppliers:{total:0,active:0},stock:[],orders:{pending:0,purchasing:0,overdue:0,month_amount:0},supplier_distribution:[],purchase_trend:[]})
  if(url.startsWith('/api/dashboard/'))return response({records:[],total:0})
  throw Error('Unexpected request '+url)
 }
 const app=mount(App)
 const send=e=>controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(e)+'\n\n'))
 try{
  await settle();await app.click('智能助手')
  app.nodes().find(n=>n.tagName==='TEXTAREA').props['onUpdate:modelValue']('采购');await settle();await app.click('发送')
  send({type:'business_result',id:'a',result:{result_id:'query',title:'物料查询',kind:'part',records:[{name:'旧内部查询资料'}],columns:[{key:'name',label:'物料名称'}],execution:{outcome:'success',message:'查询成功'}}})
  await settle();assert.doesNotMatch(rendered(app),/旧内部查询资料/)
  app.nodes().find(n=>n.tagName==='DETAILS').props.onToggle({target:{open:true}});await settle()
  assert.match(rendered(app),/旧内部查询资料/)
  send({type:'tool_start',tool_name:'purchase_request_create',tool_call_id:'write'})
  await settle();assert.doesNotMatch(rendered(app),/旧内部查询资料/)
  send({type:'tool_result',tool_name:'purchase_request_create',tool_call_id:'write',text:'业务校验失败'})
  send({type:'business_result',id:'a',result:failed})
  send({type:'tool_error',tool_call_id:'write',failure:{message:failed.execution.message}})
  send({type:'answer',id:'a',business_results_mode:'replace',content:failed.execution.message,business_results:[failed],failure_tool_call_ids:['write']})
  send({type:'done',id:'a',business_results_mode:'replace',content:failed.execution.message,business_results:[failed],failure_tool_call_ids:['write']});controller.close();await settle()
  assert.equal(rendered(app).split(failed.execution.message).length-1,1)
  assert.equal(app.nodes().filter(n=>n.props?.class?.includes?.('message-assistant')).length,1)
 }finally{try{controller.close()}catch{};await settle();app.unmount();globalThis.fetch=before}
})
