import test from 'node:test'
import assert from 'node:assert/strict'
import {replenishmentDraft} from '../src/utils/dashboard.js'
test('replenishment uses explicit identifiers and zero inventory without auto confirmation',()=>{
 const text=replenishmentDraft({id:7,name:'Part',part_code:'P7',quantity:0,stock_warning_value:10,shortage:10})
 assert.ok(text.includes('P7'))
 assert.ok(text.includes('ID 7'))
 assert.ok(text.includes('：0'))
 assert.ok(text.includes('不要直接下单'))
 assert.ok(text.includes('预计交货日期'))
 assert.ok(!text.includes('undefined'))
})
