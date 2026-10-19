import test from 'node:test'
import assert from 'node:assert/strict'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { component, mount, settle } from './component-helper.js'
import { setUser } from '../src/auth/state.js'
const user=role=>({id:7,username:'u7',display_name:'测试同事',actor:'账户#7 u7',role})
const respond=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}})
const supplier={id:2,name:'合作供应商',status:1,supplier_code:'S2'}
const parts=[{id:11,name:'物料甲',part_code:'P11',purchase_price:0,supplier_id:2},{id:12,name:'物料乙',part_code:'P12',purchase_price:8,supplier_id:2}]
function network(custom=()=>undefined){
 const before=globalThis.fetch,calls=[]
 globalThis.fetch=async(url,options={})=>{calls.push({url,options,body:options.body?JSON.parse(options.body):undefined});const result=await custom(url,options);if(result!==undefined)return result
 if(url.startsWith('/api/dashboard/suppliers?'))return respond({records:[supplier,{id:3,name:'已暂停供应商',status:2}],total:2})
 if(url.startsWith('/api/parts/page?'))return respond({code:200,data:{records:parts,total:parts.length}})
 if(url.startsWith('/api/dashboard/inventory'))return respond({records:parts.map(x=>({...x,stock_state:'normal',quantity:20})),total:2})
 if(url.startsWith('/api/dashboard/orders'))return respond({records:[{id:1,order_number:'PO1',status:1,created_by:8},{id:2,order_number:'PO2',status:1,created_by:7},{id:3,order_number:'PO3',status:2,created_by:8}],total:3})
 if(url==='/api/operations/receiving/1')return respond({id:1,order_number:'PO1',status:1,created_by:8,total_amount:10,expected_delivery_date:'2099-01-01',review_version:'a'.repeat(64),lines:[{id:1,name:'物料甲',quantity:2,unit_price:5}]})
 if(url==='/api/orders/create')return respond({code:200,data:{id:99}},201)
 if(url==='/api/operations/receiving/1/review')return respond({id:1,status:2})
 throw Error('Unexpected request '+url)
 }
 return {calls,restore(){globalThis.fetch=before}}
}
const Dashboard=await component('components/Dashboard.vue')
test('inventory normal-stock materials expose purchase, warehouse has no purchase control',async()=>{
 for(const role of ['purchaser','warehouse']){setUser(user(role));const net=network();const app=mount(Dashboard,{view:'inventory'});try{await settle();assert.equal(!!app.button('采购'),role==='purchaser');if(role==='purchaser'){await app.click('采购');assert.match(app.text(),/新建采购订单/);assert.ok(app.nodes().some(n=>n.tagName==='SELECT'&&n.props['aria-label']==='第1行物料'&&Number(n.props['onUpdate:modelValue']!==undefined)))} }finally{app.unmount();net.restore()}}
})
test('orders page offers create to purchaser and review only non-own pending orders to approver',async()=>{
 for(const role of ['purchaser','approver','warehouse']){setUser(user(role));const net=network();const app=mount(Dashboard,{view:'orders'});try{await settle();assert.equal(!!app.button('新建采购订单'),role!=='warehouse');assert.equal(app.nodes().filter(n=>n.tagName==='BUTTON'&&n.text==='审核').length,role==='approver'?1:0);if(role==='approver')assert.match(app.text(),/本人创建/)}finally{app.unmount();net.restore()}}
})
test('SSR actual purchase form includes supplier, delivery date and line fields',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue',true)
 const html=await renderToString(createSSRApp(Form));assert.match(html,/合作中供应商/);assert.match(html,/预计交货日期/);assert.match(html,/正整数/);assert.match(html,/type="date"/)
})
test('purchase form validates rows and date, adds/removes rows and sends exact camelCase payload',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue');const net=network(),saved=[];const app=mount(Form,{initialPart:parts[0],onSaved:data=>saved.push(data)})
 try{await settle();assert.doesNotMatch(app.text(),/已暂停供应商/);await app.field('预计交货日期','2000-01-01');await app.submit();assert.match(app.text(),/不能早于今天/);assert.ok(!net.calls.some(c=>c.options.method==='POST'))
 await app.field('预计交货日期','2099-01-01');await app.field('数量（正整数）',1.5);await app.submit();assert.match(app.text(),/数量必须为正整数/)
 await app.field('数量（正整数）',2);await app.field('单价',-1);await app.submit();assert.match(app.text(),/单价必须/)
 await app.field('单价','0');await app.click('添加明细');assert.equal(app.nodes().filter(n=>n.tagName==='SELECT'&&n.props['aria-label']?.endsWith('行物料')).length,2);await app.click('删除第2行');await app.submit()
 const post=net.calls.find(c=>c.url==='/api/orders/create');assert.match(post.body.orderNumber,/^PO[A-Z0-9]+$/);assert.deepEqual(post.body,{orderNumber:post.body.orderNumber,supplierId:2,expectedDeliveryDate:'2099-01-01',orderDetail:[{partId:11,quantity:2,unitPrice:'0'}]});assert.equal(post.options.credentials,'same-origin');assert.equal(saved.length,1)
 }finally{app.unmount();net.restore()}
})
test('review form loads current version, sends no actor and keeps supplier/version errors visible',async()=>{
 setUser(user('approver'));const Form=await component('components/OrderReviewForm.vue');let failure='订单内容已变化，请刷新明细后重新审核'
 const net=network((url,options)=>url.endsWith('/review')?respond({detail:failure},409):undefined);const app=mount(Form,{orderId:1})
 try{await settle();await app.field('审核意见','核对无误');await app.submit();const post=net.calls.find(c=>c.url.endsWith('/review'));assert.deepEqual(post.body,{decision:'approve',reason:'核对无误',expected_version:'a'.repeat(64)});assert.match(app.text(),/订单内容已变化/)
 failure={code:'SUPPLIER_NOT_ACTIVE',supplier_id:2,status:2};await app.click('刷新审核明细');await app.submit();assert.match(app.text(),/暂停合作/);assert.match(app.text(),/供应商 #2/)
 }finally{app.unmount();net.restore()}
})
test('review form checks fetched owner even when list allowed opening',async()=>{
 setUser(user('approver'));const Form=await component('components/OrderReviewForm.vue');const net=network(url=>url==='/api/operations/receiving/1'?respond({id:1,status:1,created_by:7,review_version:'a'.repeat(64),lines:[]}):undefined);const app=mount(Form,{orderId:1})
 try{await settle();assert.match(app.text(),/本人/);assert.ok(!app.button('确认审核'));assert.ok(!net.calls.some(c=>c.options.method==='POST'))}finally{app.unmount();net.restore()}
})
test('purchase submits multiple material lines, including zero price, and supplier switch clears old lines',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue');const net=network();const app=mount(Form,{initialPart:parts[0]})
 try{await settle();await app.field('预计交货日期','2099-01-01');await app.click('添加明细')
 const update=(label,value)=>app.nodes().find(n=>n.props?.['aria-label']===label).props['onUpdate:modelValue'](value)
 update('第2行物料',12);update('第2行数量','3');update('第2行单价','8.50');await app.submit()
 assert.deepEqual(net.calls.find(c=>c.url==='/api/orders/create').body.orderDetail,[{partId:11,quantity:1,unitPrice:'0'},{partId:12,quantity:3,unitPrice:'8.50'}])
 const supplierSelect=app.nodes().find(n=>n.tagName==='SELECT'&&!n.props['aria-label']);supplierSelect.props['onUpdate:modelValue']('');await supplierSelect.props.onChange();await settle()
 assert.equal(app.nodes().filter(n=>n.tagName==='SELECT'&&n.props['aria-label']?.endsWith('行物料')).length,1)
 await app.submit();assert.match(app.text(),/请选择|请您选择合作中的供应商/);assert.equal(net.calls.filter(c=>c.url==='/api/orders/create').length,1)
 }finally{app.unmount();net.restore()}
})
test('inactive prefilled supplier cannot create an order or load its materials',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue');const net=network(url=>url.startsWith('/api/dashboard/suppliers?')?respond({records:[{...supplier,status:2}],total:1}):undefined);const app=mount(Form,{initialPart:parts[0]})
 try{await settle();assert.match(app.text(),/不在合作中/);await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'||c.url.startsWith('/api/parts/page')))}finally{app.unmount();net.restore()}
})
test('Dashboard review succeeds in place, closes form and refreshes orders',async()=>{
 setUser(user('approver'));const net=network();const app=mount(Dashboard,{view:'orders'})
 try{await settle();await app.click('审核');assert.ok(app.nodes().some(n=>n.props?.role==='dialog'));await app.field('审核意见','已核实明细')
 const form=app.nodes().find(n=>n.tagName==='FORM'&&n.parent?.props?.role==='dialog') || app.nodes().filter(n=>n.tagName==='FORM')[0]
 await form.props.onSubmit({preventDefault(){}});await settle();assert.match(app.text(),/采购订单已审核通过/);assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'));assert.equal(net.calls.filter(c=>c.url.startsWith('/api/dashboard/orders')).length,2)
 }finally{app.unmount();net.restore()}
})
test('submission locks clicks; failed retries reuse order number and duplicate conflicts tell user to check list',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue');let finish
 const net=network(url=>url==='/api/orders/create'?new Promise(resolve=>finish=resolve):undefined);const app=mount(Form,{initialPart:parts[0]})
 try{await settle();await app.field('预计交货日期','2099-01-01');const pending=app.submit();await settle()
 assert.equal(app.button('正在提交…').props.disabled,true);assert.equal(app.button('关闭采购表单').props.disabled,true)
 await app.submit();assert.equal(net.calls.filter(c=>c.url==='/api/orders/create').length,1)
 finish(respond({detail:'服务暂不可用'},503));await pending
 const first=net.calls.find(c=>c.url==='/api/orders/create').body.orderNumber;assert.match(first,/^PO[A-Z0-9]+$/)
 const retry=app.submit();await settle();finish(respond({detail:{code:'ORDER_CONFLICT'}},409));await retry
 assert.equal(net.calls.filter(c=>c.url==='/api/orders/create')[1].body.orderNumber,first);assert.match(app.text(),/核查.*列表|列表.*核查/);assert.ok(app.text().includes(first))
 }finally{app.unmount();net.restore()}
})
test('success resets draft and next order gets new number; paged materials use inventory supplier id and never stock quantity',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue')
 const net=network(url=>{if(url.startsWith('/api/parts/page?')){const query=new URL(url,'http://test').searchParams;assert.equal(query.get('supplierId'),'2');return respond({code:200,data:{records:Number(query.get('current'))===1?[parts[1]]:[parts[0]],total:2}})}})
 const app=mount(Form,{initialPart:{...parts[0],quantity:9876,current_quantity:9876,suggested:500}})
 try{await settle();assert.equal(net.calls.filter(c=>c.url.startsWith('/api/parts/page?')).length,2);await app.field('预计交货日期','2099-01-01');await app.submit()
 const first=net.calls.find(c=>c.url==='/api/orders/create').body;assert.equal(first.orderDetail[0].quantity,1);assert.match(first.orderNumber,/^PO[A-Z0-9]+$/)
 const before=net.calls.length;await app.submit();assert.equal(net.calls.length,before)
 await app.field('预计交货日期','2099-01-02');const material=app.nodes().find(n=>n.props?.['aria-label']==='第1行物料');material.props['onUpdate:modelValue'](12);await material.props.onChange();await app.submit()
 const orders=net.calls.filter(c=>c.url==='/api/orders/create');assert.equal(orders.length,2);assert.notEqual(orders[1].body.orderNumber,first.orderNumber)
 }finally{app.unmount();net.restore()}
})

test('one supplier dropdown loads every cooperating supplier and every material across pages',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue')
 const second={id:4,name:'合作供应商乙',status:1,supplier_code:'S4'}
 const net=network(url=>{
  if(url.startsWith('/api/dashboard/suppliers?')){const query=new URL(url,'http://test').searchParams;assert.equal(query.get('status'),'1');return respond({records:query.get('current')==='1'?[supplier,{id:3,name:'已暂停供应商',status:2}]:[second],total:3})}
  if(url.startsWith('/api/parts/page?')){const query=new URL(url,'http://test').searchParams;assert.equal(query.get('supplierId'),'2');return respond({code:200,data:{records:query.get('current')==='1'?[parts[0]]:[parts[1]],total:2}})}
 })
 const app=mount(Form)
 try{
  await settle();assert.doesNotMatch(app.text(),/搜索供应商|查询供应商|已暂停供应商/)
  assert.match(app.text(),/合作供应商乙/);assert.equal(net.calls.filter(c=>c.url.startsWith('/api/dashboard/suppliers?')).length,2)
  await app.field('合作中供应商',2);await app.nodes().find(n=>n.tagName==='SELECT'&&!n.props['aria-label']).props.onChange();await settle()
  assert.match(app.text(),/物料甲/);assert.match(app.text(),/物料乙/);assert.equal(net.calls.filter(c=>c.url.startsWith('/api/parts/page?')).length,2)
 }finally{app.unmount();net.restore()}
})

test('initial loading problems have no red validation alert; invalid submission shows alert',async()=>{
 setUser(user('purchaser'));const Form=await component('components/PurchaseOrderForm.vue')
 const net=network(url=>url.startsWith('/api/parts/page?')?respond({detail:[{loc:['query','size'],msg:'invalid'}]},422):undefined)
 const app=mount(Form,{initialPart:parts[0]})
 try{
  await settle();assert.equal(app.nodes().filter(n=>n.props?.role==='alert').length,0)
  await app.field('预计交货日期','2099-01-01');await app.submit()
  assert.equal(app.nodes().filter(n=>n.props?.role==='alert').length,1)
  assert.ok(!net.calls.some(c=>c.options.method==='POST'))
 }finally{app.unmount();net.restore()}
})
