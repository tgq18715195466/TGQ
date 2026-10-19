import test from 'node:test'
import assert from 'node:assert/strict'
import {receiptPayload,changeRows} from '../src/utils/operations.js'
test('receipt preserves rejection and ignores untouched lines',()=>{
 assert.deepEqual(receiptPayload([{id:1,receive:4,reject:2},{id:2,receive:0,reject:0}]),[{detail_id:1,accepted:4,rejected:2}])
})
test('approval diff shows translated values and previous state',()=>{
 const rows=changeRows({status:3,enabled:true},{status:1,enabled:false})
 assert.equal(rows[0].before,'已终止');assert.equal(rows[0].after,'合作中')
 assert.equal(rows[1].after,'停用')
})
import {masterValues,operationKey} from '../src/utils/operations.js'
test('supplier form excludes material-only fields',()=>{
 const values=masterValues('supplier',{supplier_code:'S1',name:'Synthetic',status:1,enabled:false})
 assert.equal(values.status,1);assert.equal('enabled' in values,false)
})
test('request key supports browsers without randomUUID and fails explicitly without crypto',()=>{
 const key=operationKey({getRandomValues:array=>{array.fill(8);return array}})
 assert.equal(key.length,32)
 assert.throws(()=>operationKey({}),/浏览器/)
})
import {businessError} from '../src/utils/operations.js'
test('blocked supplier error explains actual status and next step',()=>{
 const text=businessError({code:'SUPPLIER_NOT_ACTIVE',supplier_id:15,status:3})
 assert.ok(text.includes('15'));assert.ok(text.includes('已终止'));assert.ok(text.includes('主数据维护'))
 assert.ok(!text.includes('[object Object]'))
})
test('plain validation errors stay readable',()=>{
 assert.equal(businessError('提交人与审批人不能相同'),'提交人与审批人不能相同')
 assert.equal(businessError([{msg:'数量必须大于0'}]),'数量必须大于0')
})
