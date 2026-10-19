import test from 'node:test'
import assert from 'node:assert/strict'
import {component,mount,settle,textOf} from './component-helper.js'
import {setUser} from '../src/auth/state.js'
const Banner=await component('components/InterruptBanner.vue')
const fields=[
 {path:'0.supplierId',name:'supplierId',label:'供应商编号（ID）',type:'integer',required:true,min:1,value:2,order_index:0},
 {path:'0.expectedDeliveryDate',name:'expectedDeliveryDate',label:'预计交货日期',type:'date',required:true,value:'2099-01-01',order_index:0},
 {path:'0.orderDetail.0.partId',name:'partId',label:'物料ID',type:'integer',required:true,min:1,value:11,order_index:0,detail_index:0},
 {path:'0.orderDetail.0.quantity',name:'quantity',label:'数量',type:'integer',required:true,min:1,value:2,order_index:0,detail_index:0},
 {path:'0.orderDetail.0.unitPrice',name:'unitPrice',label:'单价',type:'decimal',required:true,min:0,value:'12.00',order_index:0,detail_index:0},
]
const data={interrupt_type:'order_info_supplement',interrupt_id:'s1',fields}
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}})
const suppliers=[{id:2,name:'合作甲',supplier_code:'S2',status:1},{id:3,name:'合作乙',supplier_code:'S3',status:1},{id:4,name:'暂停供应商',status:2}]
const parts=[{id:11,name:'活塞环',part_code:'P11',supplier_id:2,enabled:true,purchase_price:'12.00'},{id:12,name:'机油泵',part_code:'P12',supplier_id:3,enabled:true,purchase_price:'9.50'},{id:13,name:'停用物料',supplier_id:3,enabled:false}]
function network(){const previous=globalThis.fetch,calls=[];setUser({id:1,username:'buyer',role:'purchaser'});globalThis.fetch=async url=>{calls.push(url);const page=new URL(url,'http://test').searchParams.get('current');if(url.startsWith('/api/dashboard/suppliers'))return response({records:page==='1'?suppliers.slice(0,1):suppliers.slice(1),total:3});if(url.startsWith('/api/parts/page'))return response({code:200,data:{records:page==='1'?parts.slice(0,1):parts.slice(1),total:3}});throw Error(url)};return {calls,restore(){globalThis.fetch=previous}}}
const control=(app,label)=>app.nodes().find(n=>n.tagName==='SELECT'&&n.props['aria-label']?.endsWith(label))

test('supplement uses full cooperating supplier catalog and supplier-specific material dropdowns',async()=>{
 const net=network(),resumed=[],app=mount(Banner,{interruptData:data,onResume:value=>resumed.push(value)})
 try{
  await settle();const supplier=control(app,'供应商'),part=control(app,'物料');assert.ok(supplier);assert.ok(part)
  assert.equal(net.calls.length,4);assert.ok(textOf(supplier).includes('合作乙'));assert.ok(!textOf(supplier).includes('暂停供应商'))
  assert.ok(textOf(part).includes('活塞环'));assert.ok(!textOf(part).includes('机油泵'))
  await supplier.props.onChange({target:{value:'3'}});await settle();assert.ok(textOf(part).includes('机油泵'));assert.ok(!textOf(part).includes('活塞环'));assert.ok(!textOf(part).includes('停用物料'))
  await app.click('提交补充信息');assert.equal(resumed.length,0)
  await part.props.onChange({target:{value:'12'}});await settle();await app.click('提交补充信息')
  assert.equal(resumed[0].values['0.supplierId'],'3');assert.equal(resumed[0].values['0.orderDetail.0.partId'],'12');assert.equal(resumed[0].values['0.orderDetail.0.unitPrice'],'9.50')
 }finally{app.unmount();net.restore()}
})

test('closing an incomplete supplement emits only close and does not resume or create an order',async()=>{
 const net=network(),closed=[],resumed=[],app=mount(Banner,{interruptData:{...data,fields:fields.map(f=>({...f,value:''}))},onClose:()=>closed.push(true),onResume:value=>resumed.push(value)})
 try{await settle();await app.click('关闭');assert.equal(closed.length,1);assert.equal(resumed.length,0);assert.ok(net.calls.every(url=>!url.includes('resume')&&!url.includes('orders/create')))}finally{app.unmount();net.restore()}
})
