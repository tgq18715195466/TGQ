import test from 'node:test'
import assert from 'node:assert/strict'
import {component,mount,settle} from './component-helper.js'
import {setUser} from '../src/auth/state.js'
const Banner=await component('components/InterruptBanner.vue')

test('warehouse never sees the purchase confirmation heading or purchase action details',async()=>{
 setUser({id:3,username:'storekeeper',role:'warehouse'})
 const previous=globalThis.fetch;globalThis.fetch=()=>assert.fail('unauthorized confirmation must not load procurement information')
 const app=mount(Banner,{interruptData:{interrupt_id:'old-hitl',interrupt_type:'hitl_approval',action_requests:[{name:'purchase_request_create',args:{part_id:1,quantity:20}}]}})
 try{await settle();assert.match(app.text(),/没有采购权限/);assert.doesNotMatch(app.text(),/需要人工确认|提交采购申请|单价|数量/);assert.equal(app.button('✓ 同意'),undefined);assert.ok(app.button('关闭'))}finally{app.unmount();globalThis.fetch=previous}
})

test('warehouse cannot fill procurement supplement or request its supplier catalog; close remains usable',async()=>{
 setUser({id:3,username:'storekeeper',role:'warehouse'})
 const previous=globalThis.fetch,requests=[],resumed=[],closed=[]
 globalThis.fetch=async url=>{requests.push(url);throw Error('warehouse must not query procurement catalog')}
 const app=mount(Banner,{interruptData:{interrupt_id:'old',interrupt_type:'order_info_supplement',fields:[{path:'0.supplierId',name:'supplierId',label:'供应商',required:true,order_index:0}]},onResume:value=>resumed.push(value),onClose:()=>closed.push(true)})
 try{
  await settle();assert.match(app.text(),/没有采购权限/)
  assert.equal(app.button('提交补充信息'),undefined)
  assert.ok(!app.nodes().some(n=>['SELECT','INPUT','TEXTAREA'].includes(n.tagName)))
  assert.equal(requests.length,0);await app.click('关闭');assert.equal(closed.length,1);assert.equal(resumed.length,0)
 }finally{app.unmount();globalThis.fetch=previous}
})

test('a stale submit handler cannot resume procurement after changing to a warehouse identity',async()=>{
 setUser({id:1,username:'buyer',role:'purchaser'})
 const resumed=[],app=mount(Banner,{interruptData:{interrupt_id:'stale',interrupt_type:'order_info_supplement',fields:[{path:'0.quantity',name:'quantity',label:'数量',type:'integer',required:true,min:1,value:2,order_index:0}]},onResume:value=>resumed.push(value)})
 try{
  await settle();const submit=app.button('提交补充信息').props.onClick
  setUser({id:3,username:'storekeeper',role:'warehouse'});await settle();await submit()
  assert.equal(resumed.length,0);assert.equal(app.button('提交补充信息'),undefined)
 }finally{app.unmount()}
})
