import test from 'node:test'
import assert from 'node:assert/strict'
import {buildDisplayItems} from '../src/utils/message-display.js'
import {upsertAnswer,upsertBusinessResult} from '../src/utils/answer-stream.js'

const query={result_id:'query',title:'物料查询',kind:'part',records:[{name:'内部资料'}],execution:{outcome:'success',message:'查询成功'}}
const failed=(id,message='合作状态校验未通过。')=>({result_id:id,title:'采购申请',kind:'purchase_request',records:[],execution:{outcome:'failure',message}})
const failure=(id,message='合作状态校验未通过。')=>({id,role:'tool',tool_name:'purchase_request_create',failure:{message,operation:'提交采购申请'}})

test('a fact-only failure owns its failure; no fallback assistant bubble',()=>{
 const items=buildDisplayItems([failure('write'),{id:'a',role:'assistant',channel:'answer',content:'',business_results:[failed('write')]}],false)
 assert.deepEqual(items.map(item=>item.kind),['message'])
})

test('a unified reply suppresses only failures it explicitly covers',()=>{
 const items=buildDisplayItems([failure('one'),failure('two'),{id:'a',role:'assistant',channel:'answer',content:'合作状态校验未通过。',failure_tool_call_ids:['one']}],false)
 assert.deepEqual(items.filter(item=>item.kind==='failure').map(item=>item.message.id),['two'])
})

test('procurement turn hides old query facts without hiding earlier readonly turns',()=>{
 const messages=[{id:'u1',role:'user',content:'查询资料'}, {id:'a1',role:'assistant',channel:'answer',content:'',business_results:[query]},
  {id:'u2',role:'user',content:'采购'}, {id:'query',role:'tool',tool_name:'part_query'}, {id:'write',role:'tool',tool_name:'purchase_request_create'},
  {id:'a2',role:'assistant',channel:'answer',content:'申请尚需确认。',business_results:[query]}]
 const items=buildDisplayItems(messages,false)
 assert.deepEqual(items.find(item=>item.message.id==='a1').message.business_results,[query])
 assert.deepEqual(items.find(item=>item.message.id==='a2').message.business_results,[])
 assert.equal(messages.at(-1).business_results.length,1,'render policy must not mutate stored history')
})

test('done snapshot removes old facts and persists explicit failure coverage',()=>{
 const messages=[]
 upsertBusinessResult(messages,{id:'a',result:query})
 upsertAnswer(messages,{id:'a',content:'',business_results_mode:'replace',business_results:[failed('write')],failure_tool_call_ids:['write']},true)
 assert.deepEqual(messages[0].business_results,[failed('write')])
 assert.deepEqual(messages[0].failure_tool_call_ids,['write'])
 upsertAnswer(messages,{id:'a',content:'',business_results_mode:'replace',business_results:[]},true)
 assert.deepEqual(messages[0].business_results,[])
})
