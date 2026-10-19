import test from 'node:test'
import assert from 'node:assert/strict'
import {component,mount,settle} from './component-helper.js'
import {setUser} from '../src/auth/state.js'

const Operations=await component('components/Operations.vue')
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}})
function network(records){
 const previous=globalThis.fetch,calls=[]
 globalThis.fetch=async(url,options={})=>{
  calls.push({url,options})
  if(url.startsWith('/api/operations/suggestions'))return response({records:[{id:1,name:'建议物料',enabled:true,supplier_status:1}],total:1})
  if(url.startsWith('/api/operations/requests'))return response({records:[{id:2,name:'申请物料',status:'pending',actor:'他人',reason:'补货'}],total:1})
  if(url.startsWith('/api/operations/receiving'))return response({records,total:records.length})
  throw Error('Unexpected URL '+url)
 }
 return {calls,restore(){globalThis.fetch=previous}}
}

test('read_procurement alone loads suggestions and request records without write controls',async()=>{
 setUser({id:7,role:'purchaser',username:'reader',permissions:['read_procurement']})
 const net=network([]),app=mount(Operations,{view:'requests'})
 try{
  await settle()
  assert.match(app.text(),/建议物料/);assert.match(app.text(),/申请物料/)
  assert.ok(net.calls.some(c=>c.url.startsWith('/api/operations/requests')))
  assert.ok(!app.button('采购'));assert.ok(!app.button('提交采购申请'));assert.ok(!app.button('咨询 Agent'))
  await app.click('查看记录')
  assert.ok(!app.button('确认审批并生成订单'))
  assert.ok(!net.calls.some(c=>c.options.method==='POST'))
 }finally{app.unmount();net.restore()}
})

test('procurement writers retain their purchase controls',async()=>{
 setUser({id:7,role:'purchaser',username:'writer'})
 const net=network([]),app=mount(Operations,{view:'requests'})
 try{await settle();assert.ok(app.button('采购'));assert.ok(app.button('咨询 Agent'));assert.ok(!app.button('查看并审批'))}
 finally{app.unmount();net.restore()}
})

test('receiving lists every material batch with its real accepted time and leaves legacy unknown',async()=>{
 setUser({id:7,role:'warehouse',username:'warehouse'})
 const net=network([
  {id:1,order_number:'PO1',part_names:'物料甲\n物料乙',receipt_items:[
   {name:'物料甲',part_code:'P1',unit:'个',accepted_batches:[
    {receipt_id:1,delivery_number:'BATCH1',accepted_quantity:3,received_at:'2026-10-08T00:00:00+08:00'},
    {receipt_id:2,delivery_number:'BATCH2',accepted_quantity:4,received_at:'2026-10-08T12:30:00+08:00'}]},
   {name:'物料乙',part_code:'P2',unit:'箱',accepted_batches:[]}]},
  {id:2,order_number:'LEGACY',status:4,expected_delivery_date:'1997-01-01',actual_delivery_date:'1997-02-01',receipt_items:[]}])
 const app=mount(Operations,{view:'receiving'})
 try{
  await settle()
  assert.match(app.text(),/实际验收入库时间/)
  const tableRows=app.nodes().filter(n=>n.tagName==='TR')
  const cells=tableRows[1].children.filter(n=>n.tagName==='TD')
  const timeCell=cells.find(n=>n.text?.includes('BATCH1'))
  assert.ok(timeCell,'batch times need their own receiving column')
  assert.match(timeCell.text,/物料甲.*P1/);assert.match(timeCell.text,/2026-10-08 00:00:00/)
  assert.match(timeCell.text,/2026-10-08 12:30:00/);assert.match(timeCell.text,/BATCH2/)
  assert.match(timeCell.text,/物料乙.*P2.*—/)
  const headers=tableRows[0].children.map(n=>n.text)
  const index=headers.indexOf('实际验收入库时间（北京时间）')
  assert.ok(index>=0)
  assert.equal(tableRows[2].children[index].text,'—')
 }finally{app.unmount();net.restore()}
})
