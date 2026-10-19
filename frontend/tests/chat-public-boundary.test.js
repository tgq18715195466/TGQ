import test from 'node:test'
import assert from 'node:assert/strict'
import {buildDisplayItems} from '../src/utils/message-display.js'
import {displayArgs,formatCollectedData} from '../src/utils/approval-display.js'

test('internal file probes never become business error banners in old history',()=>{
 const items=buildDisplayItems([{id:'f1',role:'tool',tool_name:'read_file',failure:{message:'failed'},text:'not found'},{id:'f2',role:'tool',tool_name:'order_create',failure:{message:'supplier blocked'},text:'failed'}],false)
 assert.equal(items.filter(x=>x.kind==='failure').length,1)
 assert.equal(items.find(x=>x.kind==='failure').message.tool_name,'order_create')
})
test('answers hide internal keys while keeping business data and user original text',()=>{
 const text='草稿已备好（幂等键 `replenish-PC2001-2026-001`），数量 20，供应商 SUP00026。'
 const messages=[{role:'user',content:text},{role:'assistant',channel:'answer',content:text}]
 const items=buildDisplayItems(messages,false)
 assert.equal(items[0].message.content,text)
 assert.doesNotMatch(items[1].message.content,/幂等键|replenish-PC2001/)
 assert.match(items[1].message.content,/数量 20|SUP00026/)
})
test('confirmation fields never show transport or identity overrides',()=>{
 const args=displayArgs({supplier_id:26,request_id:'secret',thread_id:'s1',created_by:999,order_detail:[{part_id:3}]})
 assert.deepEqual(args,{supplier_id:26})
 assert.doesNotMatch(formatCollectedData({request_id:'secret',supplier_id:26}),/secret|幂等/)
})
import {publicToolText} from '../src/utils/public-display.js'
test('incomplete tool argument fragments never expose generated keys',()=>{
 const text=publicToolText('{"request_id":"replenish-secret')
 assert.doesNotMatch(text,/request_id|replenish-secret/)
})

import {publicText} from '../src/utils/public-display.js'
test('historical answer displays actual status names, leaving quantities and identifiers intact',()=>{
 const text='| 字段 | 内容 |\n| --- | --- |\n| 订单状态 | **5**（已完成/结案） |\n| 供应商合作状态 | 3（已终止） |\n\n| 订单编号 | 数量 | 状态 |\n| --- | --- | --- |\n| PO123 | 30 | 4 |'
 const out=publicText(text)
 assert.match(out,/订单状态 \| 已取消/)
 assert.match(out,/供应商合作状态 \| 已终止/)
 assert.match(out,/PO123 \| 30 \| 已入库/)
 assert.equal(publicText('数量 3，金额 123。'),'数量 3，金额 123。')
})
