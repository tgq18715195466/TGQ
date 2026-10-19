import test from 'node:test'
import assert from 'node:assert/strict'
import { streamChat, resumeChat } from '../src/api/chat.js'
import { buildDisplayItems } from '../src/utils/message-display.js'

test('procurement progress reaches onProgress with Chinese stage intact for stream and resume',async()=>{
 const progress=[{type:'progress',message:'正在处理您的请求。'},
  {type:'procurement_progress',stage:'库存核验',message:'正在核验库存。'},
  {type:'procurement_progress',stage:'供应商核验',message:'正在核验供应商状态。'}]
 const old=globalThis.fetch
 globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){
  const bytes=new TextEncoder().encode([...progress,{type:'done'}].map(event=>'data: '+JSON.stringify(event)+'\r\n\r\n').join(''))
  for(let i=0;i<bytes.length;i+=3)controller.enqueue(bytes.slice(i,i+3))
  controller.close()
 }}))
 try{
  for(const resume of [false,true]){
   const seen=[],callbacks={onProgress:event=>seen.push(event),onToken:()=>assert.fail('progress must not enter model body'),onAnswer:()=>assert.fail('progress must not become an answer')}
   const final=resume?await resumeChat('s',{interrupt_id:'i',workflow_action:'retry'},callbacks):await streamChat('核验库存','s',callbacks)
   assert.deepEqual(seen,progress)
   assert.equal(final.content,'')
  }
 }finally{globalThis.fetch=old}
})

test('separate progress, tool errors and final answer across split SSE frames',async()=>{
 const events=[{type:'progress',message:'正在查询库存。'}, {type:'tool_start',tool_call_id:'t',tool_name:'part_query'}, {type:'tool_result',tool_call_id:'t',text:'failure'}, {type:'tool_error',tool_call_id:'t',failure:{message:'连接失败',retryable:true}}, {type:'answer',content:'共有三件物料。',source:'main'}, {type:'done',content:'共有三件物料。'}]
 const old=globalThis.fetch
 globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){const bytes=new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''));for(let i=0;i<bytes.length;i+=7)controller.enqueue(bytes.slice(i,i+7));controller.close()}}))
 try{
  for(const resume of [false,true]){
   const seen=[]; const callbacks={onToken:()=>assert.fail('execution must not be body text'),onAnswer:e=>seen.push(['answer',e.content]),onProgress:e=>seen.push(['progress',e.message]),onToolError:e=>seen.push(['failure',e.failure.message])}
   const result=resume?await resumeChat('s',{supplement:'x'},callbacks):await streamChat('query','s',callbacks)
   assert.equal(result.content,'共有三件物料。')
   assert.deepEqual(seen,[['progress','正在查询库存。'],['failure','连接失败'],['answer','共有三件物料。']])
  }
 }finally{globalThis.fetch=old}
})

test('failures remain visible with tool details hidden, internal messages never enter body',()=>{
 const result=buildDisplayItems([{id:'i',role:'assistant',channel:'internal',content:'new unseen English prose'}, {id:'t',role:'tool',failure:{message:'连接失败',retryable:true}}, {id:'a',role:'assistant',channel:'answer',content:'库存10件。'}],false)
 assert.deepEqual(result.map(x=>x.kind),['failure','message'])
 assert.equal(result[0].message.failure.message,'连接失败')
 assert.equal(result[1].message.content,'库存10件。')
})


test('failed submission remains a reply fallback until the business answer arrives',()=>{
 const tool={id:'t',role:'tool',failure:{operation:'提交采购申请',message:'预计交货日期不能早于今天',retryable:false}}
 const items=buildDisplayItems([tool],false);assert.equal(items[0].kind,'failure');assert.match(items[0].message.failure.message,/交货日期/)
 const answered=buildDisplayItems([tool,{id:'a',role:'assistant',channel:'answer',content:'请补充有效的预计交货日期。',failure_tool_call_ids:['t']}],false);assert.equal(answered.length,1);assert.equal(answered[0].kind,'message')
})

test('SSE deltas are visible before done; final answer reconciles without duplicate text',async()=>{
 const old=globalThis.fetch;let transport,seen=[]
 globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){transport=controller}}))
 try{
  const pending=streamChat('query','s',{onAnswerDelta:e=>seen.push(e.content),onAnswer:e=>seen.push(e.content)})
  await new Promise(resolve=>setImmediate(resolve))
  const send=e=>{const bytes=new TextEncoder().encode('data: '+JSON.stringify(e)+'\n\n');for(let i=0;i<bytes.length;i+=3)transport.enqueue(bytes.slice(i,i+3))}
  send({type:'answer_delta',id:'a',content:'库存',source:'main'})
  await new Promise(resolve=>setImmediate(resolve))
  assert.deepEqual(seen,['库存'])
  send({type:'answer_delta',id:'a',content:'10件。',source:'main'})
  send({type:'answer',id:'a',content:'库存10件。',source:'main'})
  send({type:'done'});transport.close()
  assert.equal((await pending).content,'库存10件。')
  assert.deepEqual(seen,['库存','10件。','库存10件。'])
 }finally{globalThis.fetch=old}
})
