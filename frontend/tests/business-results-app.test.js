import test from 'node:test'
import assert from 'node:assert/strict'
import { component, mount, settle } from './component-helper.js'
import { setUser } from '../src/auth/state.js'

const App=await component('App.vue')
const business={result_id:'r',title:'库存查询结果',kind:'inventory',records:[{name:'物料甲',quantity:0}],total:1,returned_count:1,complete:true,columns:[{key:'name',label:'物料名称'},{key:'quantity',label:'库存数量',format:'number'}],execution:{outcome:'success',message:'已核实。'}}
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}})
const send=(controller,event)=>controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(event)+'\n\n'))
function network(history=[],initialPending=[]){
 const before=globalThis.fetch,calls=[];let controller,pending=initialPending
 globalThis.fetch=async(url,options={})=>{
  calls.push({url,options})
  if(url==='/api/chat/stream'||url.endsWith('/resume'))return new Response(new ReadableStream({start(c){controller=c}}))
  if(url.startsWith('/api/dashboard/inventory'))return response({records:[],total:0})
  if(url.startsWith('/api/dashboard/suppliers'))return response({records:[{id:2,name:'供应商甲',status:1,supplier_code:'S2'}],total:1})
  if(url.startsWith('/api/parts/page'))return response({code:200,data:{records:[],total:0}})
  if(url.endsWith('/messages'))return response({messages:history,interrupts:pending})
  if(url.startsWith('/api/history'))return response({sessions:history.length?[{thread_id:'saved',title:'已保存对话'}]:[]})
  throw Error('Unexpected request '+url)
 }
 return {calls,send(event){if(event.type==='interrupt')pending=[event];send(controller,event)},close(){controller.close()},restore(){globalThis.fetch=before}}
}
const user=()=>setUser({id:7,username:'u7',display_name:'测试同事',role:'warehouse'})
async function start(app){
 await settle();await app.click('智能助手')
 app.nodes().find(n=>n.tagName==='TEXTAREA').props['onUpdate:modelValue']('查询库存');await settle();await app.click('发送')
}

const purchasePending=kind=>({type:'interrupt',interrupt_type:kind,interrupt_id:'old-purchase',thread_id:'saved',fields:[{path:'0.supplierId',name:'supplierId',required:true,order_index:0}],action_requests:[{name:'purchase_request_create',args:{part_id:1,quantity:2}}]})
const renderedText=app=>app.text()+app.nodes().map(n=>n.props?.innerHTML||'').join('')
for(const kind of ['order_info_supplement','hitl_approval']){
 test(`warehouse receives ${kind} as a permission reply, never a procurement dialog`,async()=>{
  user();const net=network(),app=mount(App)
  try{
   await start(app);net.send(purchasePending(kind));net.send({type:'done',id:'denied',thread_id:'saved'});net.close();await settle()
   assert.match(renderedText(app),/没有采购权限/)
   assert.ok(!app.nodes().some(n=>String(n.props?.class||'').includes('interrupt-workspace')))
   assert.equal(app.button('提交补充信息'),undefined);assert.equal(app.button('✓ 同意'),undefined)
   assert.ok(!net.calls.some(c=>c.url.startsWith('/api/dashboard/suppliers')||c.url.startsWith('/api/parts/page')||c.url.endsWith('/resume')))
   await app.click('新建仓管对话');assert.ok(app.nodes().some(n=>n.tagName==='TEXTAREA'))
  }finally{app.unmount();net.restore()}
 })
}
test('reopening a saved unauthorized procurement checkpoint does not reopen its confirmation window',async()=>{
 user();const net=network([{id:'old',role:'user',content:'之前的采购需求'}],[purchasePending('hitl_approval')]),app=mount(App)
 try{
  await settle();await app.click('智能助手');let node=app.nodes().find(n=>n.text==='已保存对话');while(node&&!node.props?.onClick)node=node.parent
  await node.props.onClick({stopPropagation(){},preventDefault(){}});await settle()
  assert.match(renderedText(app),/没有采购权限/)
  assert.ok(!app.nodes().some(n=>String(n.props?.class||'').includes('interrupt-workspace')))
  await app.click('新建仓管对话');assert.ok(app.nodes().some(n=>n.tagName==='TEXTAREA'))
 }finally{app.unmount();net.restore()}
})

test('actual App keeps early evidence collapsed in one bubble and retains it after done',async()=>{
 user();const net=network(),app=mount(App)
 try {
  await start(app);net.send({type:'business_result',id:'a',result:business});await settle()
  assert.match(app.text(),/查看查询依据/);assert.doesNotMatch(app.text(),/物料甲|分析说明/)
  net.send({type:'done',id:'a',thread_id:'s'});net.close();await settle()
  assert.match(app.text(),/查看查询依据/)
  app.nodes().find(n=>n.tagName==='DETAILS').props.onToggle({target:{open:true}});await settle()
  assert.match(app.text(),/物料甲/)
  assert.equal(app.nodes().filter(n=>n.props?.class?.includes?.('message-assistant')).length,1)
  assert.ok(!net.calls.some(c=>c.url.endsWith('/messages')),'successful business result must not be replaced by history reload')
 } finally {app.unmount();net.restore()}
})

test('actual App restores done-only business results and final text in same bubble',async()=>{
 user();const net=network(),app=mount(App)
 try {
  await start(app);net.send({type:'done',id:'a',thread_id:'s',content:'请您核对库存。',business_results:[business]});net.close();await settle()
  assert.match(app.text(),/查看查询依据/);assert.doesNotMatch(app.text(),/物料甲|分析说明/)
  assert.equal(app.nodes().filter(n=>n.props?.class?.includes?.('message-assistant')).length,1)
 } finally {app.unmount();net.restore()}
})

test('later tool messages do not replace an early answer bubble through history reload',async()=>{
 user();const net=network(),app=mount(App)
 try {
  await start(app);net.send({type:'business_result',id:'a',result:business})
  net.send({type:'tool_start',tool_call_id:'t',tool_name:'part_query',source:'main'})
  net.send({type:'answer_delta',id:'a',content:'请您核对库存。'})
  net.send({type:'answer',id:'a',content:'请您核对库存。'})
  net.send({type:'done',id:'a'});net.close();await settle()
  assert.ok(!net.calls.some(c=>c.url.endsWith('/messages')),'later tools must not force a typewriter/history reset')
  assert.match(app.text(),/查看查询依据/);assert.doesNotMatch(app.text(),/物料甲|分析说明/)
 } finally {app.unmount();net.restore()}
})

test('actual App reloads saved answer with evidence collapsed but still available',async()=>{
 user();const net=network([{id:'a',role:'assistant',channel:'answer',source:'main',content:'请您核对库存。',business_results:[business]}]),app=mount(App)
 try {
  await settle();await app.click('智能助手')
  const title=app.nodes().find(n=>n.text==='已保存对话');assert.ok(title)
  let node=title;while(node&&!node.props?.onClick)node=node.parent
  await node.props.onClick({stopPropagation(){},preventDefault(){}});await settle()
  assert.match(app.text(),/查看查询依据/);assert.doesNotMatch(app.text(),/物料甲|分析说明/)
  assert.match(renderedText(app),/请您核对库存/)
  app.nodes().find(n=>n.tagName==='DETAILS').props.onToggle({target:{open:true}});await settle()
  assert.match(app.text(),/物料甲/)
 } finally {app.unmount();net.restore()}
})

test('App closes supplement without resuming, shows chat again and reopens the same draft',async()=>{
 setUser({id:7,username:'u7',display_name:'测试同事',role:'purchaser'})
 const net=network(),app=mount(App)
 try{
  await start(app)
  net.send({type:'interrupt',thread_id:'s',interrupt_type:'order_info_supplement',interrupt_id:'form1',fields:[{path:'0.supplierId',name:'supplierId',label:'供应商编号（ID）',order_index:0,type:'integer',required:true,value:2,min:1},{path:'0.expectedDeliveryDate',name:'expectedDeliveryDate',label:'预计交货日期',order_index:0,type:'date',required:true,value:''}]})
  net.close();await settle()
  const date=app.nodes().find(n=>n.props?.['aria-label']==='订单1 预计交货日期');assert.ok(date)
  date.props.onInput({target:{value:'2099-01-01'}});await settle();await app.click('关闭')
  const panel=app.nodes().find(n=>n.props?.class==='interrupt-workspace');assert.equal(panel.style.display,'none')
  const workspace=app.nodes().find(n=>typeof n.props?.class==='string'&&n.props.class.includes('chat-workspace'));assert.ok(!workspace.props.class.includes('interrupt-open'))
  assert.ok(app.button('继续补充信息'));assert.ok(!net.calls.some(c=>c.url.endsWith('/resume')))
  await app.click('继续补充信息');assert.notEqual(panel.style.display,'none');assert.equal(date.props.value,'2099-01-01')
 }finally{app.unmount();net.restore()}
})
