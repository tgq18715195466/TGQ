import test from 'node:test'
import assert from 'node:assert/strict'
import { component, mount, settle, textOf } from './component-helper.js'
import { setUser } from '../src/auth/state.js'

const Dashboard=await component('components/Dashboard.vue')
const Operations=await component('components/Operations.vue')
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}})
const parts=[{id:7,name:'活塞环组件',part_code:'PC2001',model:'CB400',quantity:16}, {id:29,name:'活塞总成',part_code:'PC2029',model:'CG125',quantity:3}]
const account=role=>({id:10,username:'test',display_name:'测试同事',role})
function network(initial=parts){
 const inventory=initial.map(part=>({...part}))
 const previous=globalThis.fetch,calls=[]
 globalThis.fetch=async(url,options={})=>{
  calls.push({url,options,body:options.body?JSON.parse(options.body):null})
  if(url.startsWith('/api/dashboard/inventory')){
   const page=new URL(url,'http://test').searchParams.get('current')
   return response({records:page==='2'?[inventory[1]]:[inventory[0]],total:2})
  }
  if(url.startsWith('/api/operations/movements'))return response({records:[],total:0})
  if(url==='/api/operations/stock'){
   const body=JSON.parse(options.body),part=inventory.find(p=>p.id===body.part_id)
   part.quantity=body.kind==='count'?body.quantity:part.quantity+(body.kind==='issue'?-body.quantity:body.quantity)
   return response({id:5})
  }
  throw Error('Unexpected request '+url)
 }
 return {calls,restore(){globalThis.fetch=previous}}
}
const field=(app,label)=>app.nodes().find(n=>n.props?.['aria-label']===label)
async function update(app,label,value){const node=field(app,label);assert.ok(node,'Missing '+label);if(node.props['onUpdate:modelValue'])node.props['onUpdate:modelValue'](value);await node.props.onChange?.({target:{value:String(value)}});await settle()}

test('inventory hides empty actions for warehouse and preserves purchase actions for purchaser',async()=>{
 for(const role of ['warehouse','purchaser']){
  setUser(account(role));const net=network(),app=mount(Dashboard,{view:'inventory'})
  try{await settle();assert.equal(app.nodes().some(n=>n.tagName==='TH'&&textOf(n)==='操作'),role==='purchaser');assert.equal(!!app.button('采购'),role==='purchaser')}
  finally{app.unmount();net.restore()}
 }
})

test('stock form loads every material in a single dropdown and submits the selected internal ID',async()=>{
 setUser(account('warehouse'));const net=network(),app=mount(Operations,{view:'stock'})
 try{
  await settle();assert.doesNotMatch(app.text(),/物料ID/)
  assert.equal(net.calls.filter(c=>c.url.startsWith('/api/dashboard/inventory')).length,2)
  assert.doesNotMatch(app.text(),/查询物料/);assert.ok(!app.button('查询'))
  assert.equal(app.nodes().filter(n=>n.tagName==='INPUT'&&n.props.type==='search').length,0)
  const options=()=>field(app,'作业物料').children.filter(n=>n.tagName==='OPTION').map(textOf)
  assert.ok(options().some(x=>x.includes('活塞环组件')));assert.ok(options().some(x=>x.includes('活塞总成')))
  assert.equal(options().length,3)
  assert.equal(new URL(net.calls.filter(c=>c.url.startsWith('/api/operations/movements')).at(-1).url,'http://test').searchParams.has('part_id'),false)
  await update(app,'作业物料',29)
  await app.field('操作数量',3);await app.field('关联单据号','领料单-测试');await app.field('操作原因','维修领料')
  await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'));assert.match(app.text(),/请.*填写.*领料人/)
  await app.field('领料人姓名','  张三  ')
  await app.submit()
  const post=net.calls.find(c=>c.url==='/api/operations/stock');assert.equal(post.body.part_id,29);assert.equal(post.body.quantity,3);assert.equal(post.body.kind,'issue');assert.equal('expected_quantity' in post.body,false)
  assert.equal(post.body.recipient_name,'张三');assert.equal('actor' in post.body,false)
  await app.field('操作类型','return');await settle();assert.doesNotMatch(app.text(),/领料人姓名/)
  await app.field('操作数量',1);await app.field('操作原因','维修退库');await app.submit()
  const returned=net.calls.filter(c=>c.url==='/api/operations/stock').at(-1);assert.equal(returned.body.kind,'return');assert.equal('recipient_name' in returned.body,false)
 }finally{app.unmount();net.restore()}
})

test('stock operation cannot submit an unselected or unknown material',async()=>{
 setUser(account('warehouse'));const net=network(),app=mount(Operations,{view:'stock'})
 try{
  await settle();await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'));assert.match(app.text(),/请选择.*物料/)
  await update(app,'作业物料',9999);await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'))
 }finally{app.unmount();net.restore()}
})

const quantityInput=app=>app.nodes().find(n=>n.tagName==='INPUT'&&n.props.type==='number')
async function fillIssue(app,quantity){
 await update(app,'作业物料',7)
 await app.field('领料人姓名','张三');await app.field('操作数量',quantity)
 await app.field('关联单据号','ISSUE-TEST');await app.field('操作原因','维修领料')
}

test('issue quantity cannot exceed stock; exact stock is allowed and refreshes to zero',async()=>{
 setUser(account('warehouse'));const net=network(),app=mount(Operations,{view:'stock'})
 try{
  await settle();await fillIssue(app,17)
  assert.equal(quantityInput(app).props.max,16)
  assert.match(app.text(),/当前库存[：:]\s*16/)
  assert.match(app.text(),/不能大于当前库存/)
  assert.ok(app.button('确认提交库存变更').props.disabled)
  await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'))
  await app.field('操作数量',16)
  assert.equal(!!app.button('确认提交库存变更').props.disabled,false)
  await app.submit();await settle()
  assert.equal(net.calls.filter(c=>c.options.method==='POST').length,1)
  assert.equal(quantityInput(app).props.max,0)
  assert.ok(app.button('确认提交库存变更').props.disabled)
 }finally{app.unmount();net.restore()}
})

test('switching materials updates issue limit while return and count can exceed current stock',async()=>{
 setUser(account('warehouse'));const net=network(),app=mount(Operations,{view:'stock'})
 try{
  await settle();await fillIssue(app,10)
  await update(app,'作业物料',29)
  assert.equal(quantityInput(app).props.max,3)
  await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'))
  for(const kind of ['return','count']){
   await app.field('操作类型',kind);await settle()
   assert.equal(quantityInput(app).props.max,undefined)
   await app.field(kind==='count'?'盘点实存数量':'操作数量',20)
   await app.field('操作原因','核实库存');await app.submit()
   assert.equal(net.calls.filter(c=>c.options.method==='POST').at(-1).body.kind,kind)
  }
 }finally{app.unmount();net.restore()}
})

for(const quantity of [0,null,undefined])test(`zero or unknown stock blocks issue: ${quantity}`,async()=>{
 setUser(account('warehouse'));const net=network([{...parts[0],quantity},parts[1]]),app=mount(Operations,{view:'stock'})
 try{
  await settle();await fillIssue(app,1)
  assert.ok(app.button('确认提交库存变更').props.disabled)
  await app.submit();assert.ok(!net.calls.some(c=>c.options.method==='POST'))
  assert.match(app.text(),quantity===0?/库存为零/:/库存.*未.*核实|库存.*未知/)
 }finally{app.unmount();net.restore()}
})
