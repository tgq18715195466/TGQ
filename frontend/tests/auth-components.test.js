import test from 'node:test'
import assert from 'node:assert/strict'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { component, mount, settle } from './component-helper.js'
import { auth, setUser, clearSession } from '../src/auth/state.js'
import { navigationFor } from '../src/auth/permissions.js'
import { apiFetch } from '../src/api/http.js'
const user=(role,id=7)=>({id,role,actor:`账户#${id} u${id}`,username:`u${id}`,display_name:'测试同事'})
const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}})
const sample={id:1,name:'测试物料',part_code:'P1',stock_state:'low',quantity:2,stock_warning_value:8,suggested:6,enabled:true,supplier_status:1}
const summary={suppliers:{total:2,active:2},stock:[{stock_state:'low',count:1}],orders:{pending:1,purchasing:0,overdue:0,month_amount:0},supplier_distribution:[],purchase_trend:[]}
function mockNetwork(overrides=()=>undefined){
 const calls=[];const before=globalThis.fetch
 globalThis.fetch=async(url,options={})=>{
  calls.push({url,options,body:options.body?JSON.parse(options.body):undefined})
  const custom=await overrides(url,options);if(custom!==undefined)return custom
  if(url.includes('/summary'))return response(summary)
  if(url.startsWith('/api/dashboard/inventory'))return response({records:[sample],total:1})
  if(url.startsWith('/api/history'))return response({sessions:[],messages:[]})
  if(url.startsWith('/api/operations/'))return response({records:[],total:0})
  throw Error('Unexpected URL '+url)
 }
 return {calls,restore(){globalThis.fetch=before}}
}
const Sidebar=await component('components/Sidebar.vue',true)
const ShellSSR=await component('AuthShell.vue',true)
const Banner=await component('components/InterruptBanner.vue',true)
const App=await component('App.vue')
const Shell=await component('AuthShell.vue')
const Dashboard=await component('components/Dashboard.vue')
const Operations=await component('components/Operations.vue')
const Account=await component('components/AccountSettings.vue')
for(const role of ['purchaser','warehouse','approver','admin'])test('SSR actual navigation for '+role,async()=>{
 setUser(user(role))
 const html=await renderToString(createSSRApp(Sidebar,{navigationGroups:navigationFor(auth.user)}))
 assert.match(html,/首页/);assert.match(html,/智能助手/);assert.match(html,/账户设置/)
 assert.match(html,/采购管理/);if(role==='warehouse')assert.match(html,/基础资料/)
})
test('SSR login and setup never render business shell before authentication',async()=>{
 clearSession();auth.ready=true;auth.setupRequired=false
 let html=await renderToString(createSSRApp(ShellSSR));assert.match(html,/欢迎登录/);assert.doesNotMatch(html,/工作空间|采购订单/);assert.doesNotMatch(html,/value="[^" ]+"/)
 auth.setupRequired=true;html=await renderToString(createSSRApp(ShellSSR));assert.match(html,/创建首个管理员|确认密码/);assert.doesNotMatch(html,/工作空间/)
})
test('Agent execution confirmation follows business permission, formal self-review stays separate',async()=>{
 const interruptData={interrupt_type:'hitl_approval',interrupt_id:'i',actor:'账户#7 u7',action_requests:[{name:'order_create',args:{created_by:7}}]}
 for(const role of ['purchaser','admin','approver']){
  setUser(user(role));const html=await renderToString(createSSRApp(Banner,{interruptData}));assert.match(html,/approve-btn/);assert.match(html,/待审核/)
 }
 setUser(user('warehouse'));assert.doesNotMatch(await renderToString(createSSRApp(Banner,{interruptData})),/approve-btn/)
 const inventory={...interruptData,action_requests:[{name:'inventory_adjust',args:{}}]}
 assert.match(await renderToString(createSSRApp(Banner,{interruptData:inventory})),/approve-btn/)
 setUser(user('purchaser'));assert.doesNotMatch(await renderToString(createSSRApp(Banner,{interruptData:inventory})),/approve-btn/)
})
test('warehouse App renders procurement read metrics while inventory actions remain available',async()=>{
 setUser(user('warehouse'));const network=mockNetwork();const app=mount(App)
 try{await settle();assert.match(app.text(),/业务总览/);assert.match(app.text(),/供应商总数/);assert.ok(!app.button('新建采购订单'))
 assert.ok(network.calls.some(c=>c.url.startsWith('/api/dashboard/summary')));assert.ok(network.calls.every(c=>!c.options.method||c.options.method==='GET'))
 await app.click('库存管理');assert.ok(!app.button('采购申请'));assert.ok(app.button('入库收货'));assert.ok(app.button('库存流水'))
 await app.click('库存流水');assert.match(app.text(),/当前操作身份：测试同事（u7）/)
 const readonly=app.nodes().find(n=>n.tagName==='INPUT'&&n.props.readonly!==undefined);assert.equal(readonly.props.value,'测试同事（u7）');assert.equal(readonly.props['onUpdate:modelValue'],undefined)
 }finally{app.unmount();network.restore()}
})
test('purchaser App can navigate procurement and read inventory but has no receiving or adjustment',async()=>{
 setUser(user('purchaser'));const network=mockNetwork();const app=mount(App)
 try{await settle();await app.click('采购管理');assert.ok(app.button('采购订单'));assert.ok(app.button('采购申请'));await app.click('库存管理');assert.ok(!app.button('入库收货'));assert.ok(!app.button('库存流水'));assert.ok(!app.button('确认提交库存变更'))}
 finally{app.unmount();network.restore()}
})
test('warehouse inventory warnings do not offer procurement actions',async()=>{
 setUser(user('warehouse'));const network=mockNetwork();const app=mount(Dashboard,{view:'inventory'})
 try{await settle();assert.match(app.text(),/测试物料/);assert.ok(!app.button('采购申请'));assert.ok(!app.button('咨询 Agent'))}finally{app.unmount();network.restore()}
})
test('actual pending request detail blocks own review and hides review for purchaser',async()=>{
 for(const role of ['admin','purchaser']){
  setUser(user(role));const network=mockNetwork(url=>url.startsWith('/api/operations/requests')?response({records:[{id:3,actor:auth.user.actor,status:'pending',name:'测试申请',reason:'测试原因'}],total:1}):undefined)
  const app=mount(Operations,{view:'requests'})
  try{await settle();assert.ok(!app.button('查看并审批'));await app.click('查看记录');assert.match(app.text(),role==='admin'?/不能审批本人/:/等待审批人审核/);assert.ok(!app.button('确认审批并生成订单'))}finally{app.unmount();network.restore()}
 }
})
test('account password change posts exact fields and returns to login after cookie invalidation',async()=>{
 setUser(user('warehouse'));const network=mockNetwork(url=>url==='/api/auth/password'?response({user:null}):undefined);const app=mount(Account,{view:'password'})
 try{await app.field('当前密码','old-password-123');await app.field('新密码','new-password-456');await app.field('确认新密码','new-password-456');await app.submit();assert.deepEqual(network.calls[0].body,{old_password:'old-password-123',new_password:'new-password-456'});assert.equal(auth.user,null);assert.match(auth.notice,/密码已更新/)}finally{app.unmount();network.restore()}
})
test('shared fetch clears session on 401 and rejects stale JSON after account switch',async()=>{
 setUser(user('purchaser'));const network=mockNetwork(()=>response({},401))
 try{await assert.rejects(apiFetch('/api/history'),/重新登录/);assert.equal(auth.user,null)}finally{network.restore()}
 setUser(user('purchaser'));let finish
 const slow=mockNetwork(()=>({ok:true,status:200,json:()=>new Promise(resolve=>finish=resolve)}))
 try{const res=await apiFetch('/api/history');const data=res.json();setUser(user('warehouse',8));finish({sessions:[{thread_id:'other-user'}]});await assert.rejects(data,{name:'AbortError'});assert.equal(auth.user.id,8)}finally{slow.restore()}
})
test('setup form sends entered identity and mounts App only after success',async()=>{
 clearSession();auth.ready=false
 const network=mockNetwork((url)=>url==='/api/auth/status'?response({setup_required:true}):url==='/api/auth/setup'?response({user:user('admin')}):undefined)
 const app=mount(Shell)
 try{await settle();assert.match(app.text(),/创建首个管理员/);assert.equal(network.calls.length,1)
 await app.field('用户名','new-admin');await app.field('显示名称','新管理员');await app.field('密码','secure-pass-1234');await app.field('确认密码','different-pass');await app.submit();assert.match(app.text(),/两次密码不一致/);assert.equal(network.calls.length,1)
 await app.field('确认密码','secure-pass-1234');await app.submit();const sent=network.calls.find(c=>c.url==='/api/auth/setup');assert.deepEqual(sent.body,{username:'new-admin',display_name:'新管理员',password:'secure-pass-1234'});assert.equal(sent.options.credentials,'same-origin');assert.equal(sent.options.headers.Authorization,undefined);assert.ok(!app.text().includes('创建首个管理员'));assert.ok(app.button('退出登录'))
 }finally{app.unmount();network.restore()}
})
test('login failure is Chinese; logout unmounts drafts/history and new account gets own history',async()=>{
 clearSession();auth.ready=false;let current=null,allowLogin=false
 const network=mockNetwork((url,options)=>{
  if(url==='/api/auth/status')return response({setup_required:false})
  if(url==='/api/auth/me')return response({},401)
  if(url==='/api/auth/login'){if(!allowLogin)return response({},401);current=JSON.parse(options.body).username==='u7'?user('purchaser'):user('warehouse',8);return response({user:current})}
  if(url==='/api/auth/logout'){current=null;return response({user:null})}
  if(url.startsWith('/api/history'))return response({sessions:current?[{thread_id:'thread-'+current.id,title:'私有会话'+current.id}]:[]})
 })
 const app=mount(Shell)
 try{await settle();assert.ok(network.calls.every(c=>c.url.startsWith('/api/auth/')))
 await app.field('用户名','u7');await app.field('密码','some-password');await app.submit();assert.match(app.text(),/用户名或密码不正确/);assert.equal(auth.user,null)
 allowLogin=true;await app.submit();assert.match(app.text(),/私有会话7/);await app.click('退出登录');assert.match(app.text(),/欢迎登录/);assert.doesNotMatch(app.text(),/私有会话7|工作空间/)
 await app.field('用户名','u8');await app.field('密码','another-password');await app.submit();assert.match(app.text(),/私有会话8/);assert.doesNotMatch(app.text(),/私有会话7/);assert.equal(auth.user.id,8)
 }finally{app.unmount();network.restore()}
})
test('admin account component creates users and saves role, disable and password reset',async()=>{
 setUser(user('admin'));const existing={...user('purchaser',8),enabled:true}
 const network=mockNetwork((url,options)=>{
  if(url==='/api/auth/users')return response(options.method==='POST'?{user:existing}:{users:[existing]})
  if(url==='/api/auth/users/8'){Object.assign(existing,JSON.parse(options.body));return response({user:existing})}
 })
 const app=mount(Account,{view:'users'})
 try{await settle();await app.field('用户名','new-user');await app.field('显示名称','新同事');await app.field('初始密码','initial-pass-123');await app.field('角色','warehouse');await app.submit()
 const created=network.calls.find(c=>c.options.method==='POST');assert.deepEqual(created.body,{username:'new-user',display_name:'新同事',password:'initial-pass-123',role:'warehouse'})
 await app.field('账号角色','approver');await app.field('账号状态',false);await app.field('重置密码','reset-pass-123');await app.submit(1)
 const patched=network.calls.find(c=>c.options.method==='PATCH');assert.deepEqual(patched.body,{role:'approver',enabled:false,password:'reset-pass-123'});assert.match(app.text(),/已停用/)
 assert.ok(!app.nodes().some(n=>n.tagName==='INPUT'&&n.props.value==='reset-pass-123'))
 }finally{app.unmount();network.restore()}
})
test('account management is absent for all non-admin roles',async()=>{
 for(const role of ['warehouse','purchaser','approver']){setUser(user(role));const network=mockNetwork();const app=mount(Account,{view:'users'});try{await settle();assert.ok(!app.button('创建账号'));assert.equal(network.calls.length,0)}finally{app.unmount();network.restore()}}
})
test('approver can review another applicant with readonly identity and no actor payload',async()=>{
 setUser(user('approver'));const network=mockNetwork((url,options)=>{
  if(url==='/api/operations/requests/3/review')return response({order_id:9})
  if(url.startsWith('/api/operations/requests'))return response({records:[{id:3,actor:'账户#8 u8',status:'pending',name:'测试申请',reason:'测试原因'}],total:1})
 })
 const app=mount(Operations,{view:'requests'})
 try{await settle();await app.click('查看并审批');const readonly=app.nodes().find(n=>n.tagName==='INPUT'&&n.props.readonly!==undefined);assert.equal(readonly.props.value,'测试同事（u7）');await app.field('审批意见','同意申请');await app.submit();const call=network.calls.find(c=>c.options.method==='POST');assert.deepEqual(call.body,{decision:'approve',reason:'同意申请'})}finally{app.unmount();network.restore()}
})
test('warehouse can view receiving order but cannot review it',async()=>{
 setUser(user('warehouse'));const network=mockNetwork(url=>url==='/api/operations/receiving/1'?response({id:1,status:1,order_number:'PO1',lines:[],receipts:[]}):url.startsWith('/api/operations/receiving')?response({records:[{id:1,order_number:'PO1'}],total:1}):undefined)
 const app=mount(Operations,{view:'receiving'})
 try{await settle();await app.click('明细 / 收货');assert.ok(!app.button('确认审核订单'));assert.match(app.text(),/等待审批人审核/);assert.ok(app.button('确认本批收货并入库').props.disabled)}finally{app.unmount();network.restore()}
})
test('shared fetch aborts outstanding requests on logout; stale 401 cannot log out new user',async()=>{
 setUser(user('purchaser'));let finish
 const network=mockNetwork(()=>new Promise(resolve=>finish=resolve))
 try{const pending=apiFetch('/api/history');const signal=network.calls[0].options.signal;setUser(user('warehouse',8));assert.equal(signal.aborted,true);finish(response({},401));await assert.rejects(pending,{name:'AbortError'});assert.equal(auth.user.id,8)}finally{network.restore()}
})


test('receiving displays material context and order number; delivery number and reason stay independent',async()=>{
 setUser(user('warehouse'))
 const detail={id:1,status:2,order_number:'PO-TEST',lines:[{id:9,name:'离合器',part_code:'PC1',model:'MODEL-A',supplier_name:'测试供应商',quantity:10,unit_price:2,accepted:0,remaining:10}],receipts:[{id:123,detail_id:9,delivery_number:'DN-OLD',reason:'原说明',accepted:1,rejected:0}]}
 const network=mockNetwork(url=>url==='/api/operations/receiving/1'?response(detail):url==='/api/operations/receipts'?response({id:2}):url.startsWith('/api/operations/receiving')?response({records:[{id:1,order_number:'PO-TEST',part_names:'离合器',models:'MODEL-A',supplier_names:'测试供应商'}],total:1}):undefined)
 const app=mount(Operations,{view:'receiving'})
 try{
  await settle();assert.match(app.text(),/MODEL-A/);assert.match(app.text(),/测试供应商/)
  await app.click('明细 / 收货');assert.match(app.text(),/订单编号/);assert.ok(!app.text().includes('收货单号'));assert.match(app.text(),/DN-OLD/)
  await app.field('本批送货单号','DN-NEW');await app.field('收货 / 拒收说明','验收合格')
  const receive=app.nodes().find(n=>n.tagName==='INPUT'&&n.props.max===10);receive.props['onUpdate:modelValue'](4)
  await app.submit();const sent=network.calls.find(x=>x.url==='/api/operations/receipts'&&x.options.method==='POST')
  assert.equal(sent.body.delivery_number,'DN-NEW');assert.equal(sent.body.reason,'验收合格');assert.equal(sent.body.lines[0].accepted,4)
 }finally{app.unmount();network.restore()}
})


test('purchase order details open as a dialog and close without changing the list',async()=>{
 setUser(user('purchaser'))
 const network=mockNetwork(url=>url==='/api/dashboard/orders/1'?response({id:1,order_number:'PO-MODAL',status:2,total_amount:20,details:[{id:9,part_code:'PC1',part_name:'测试物料',supplier_name:'测试供应商',quantity:10,unit_price:2,subtotal:20}]}):url.startsWith('/api/dashboard/orders')?response({records:[{id:1,order_number:'PO-MODAL',status:2}],total:1}):undefined)
 const app=mount(Dashboard,{view:'orders'})
 try{
  await settle();assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'))
  await app.click('查看详情');const dialog=app.nodes().find(n=>n.props?.role==='dialog')
  assert.ok(dialog);assert.equal(dialog.props['aria-modal'],'true');assert.match(app.text(),/测试供应商/);assert.equal(dialog.props.class,'order-detail')
  await app.click('关闭');assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'));assert.ok(app.button('查看详情'))
  await app.click('查看详情');const overlay=app.nodes().find(n=>n.props?.class==='detail-overlay')
  overlay.props.onKeydown({key:'Escape',stopPropagation(){},preventDefault(){}});await settle()
  assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'))
 }finally{app.unmount();network.restore()}
})


test('purchaser can open order form from suggestions even without a replenishment gap',async()=>{
 setUser(user('purchaser'))
 const row={...sample,supplier_id:1,purchase_price:2,suggested:0,pending_quantity:0,on_order:80,current_quantity:84}
 const network=mockNetwork(url=>url.startsWith('/api/operations/suggestions')?response({records:[row],total:1}):url.startsWith('/api/dashboard/suppliers')?response({records:[{id:1,name:'测试供应商',status:1}],total:1}):url.startsWith('/api/parts/page')?response({records:[{id:1,name:'测试物料',part_code:'P1',purchase_price:2}],total:1}):undefined)
 const app=mount(Operations,{view:'requests'})
 try{await settle();assert.equal(!!app.button('采购').props.disabled,false);assert.ok(!app.button('申请采购'));assert.ok(!app.text().includes('暂无补货缺口'));const centered=app.nodes().filter(n=>['TH','TD'].includes(n.tagName)&&n.props?.class==='centered');assert.equal(centered.length,12);await app.click('采购');assert.ok(app.nodes().some(n=>n.props?.role==='dialog'));assert.match(app.text(),/新建采购订单/)}finally{app.unmount();network.restore()}
})


test('receiving opens a modal with order data and closes via button or Escape',async()=>{
 setUser(user('warehouse'))
 const network=mockNetwork(url=>url==='/api/operations/receiving/1'?response({id:1,status:2,order_number:'PO-RECEIPT',lines:[],receipts:[]}):url.startsWith('/api/operations/receiving')?response({records:[{id:1,order_number:'PO-RECEIPT'}],total:1}):undefined)
 const app=mount(Operations,{view:'receiving'})
 try{await settle();await app.click('明细 / 收货');let dialog=app.nodes().find(n=>n.props?.role==='dialog');assert.ok(dialog);assert.equal(dialog.props['aria-modal'],'true');assert.match(app.text(),/收货：PO-RECEIPT/);await app.click('关闭');assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'));await app.click('明细 / 收货');const overlay=app.nodes().find(n=>n.props?.class==='receipt-overlay');overlay.props.onKeydown({key:'Escape',stopPropagation(){},preventDefault(){}});await settle();assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'));assert.ok(app.button('明细 / 收货'))}finally{app.unmount();network.restore()}
})


test('approver edits supplier in a dialog and saves directly with an applied record',async()=>{
 setUser(user('approver'))
 const network=mockNetwork((url,options)=>url==='/api/operations/master-changes'&&options.method==='POST'?response({id:12,status:'applied',entity_id:1}):url.startsWith('/api/operations/master/supplier')?response({records:[{id:1,supplier_code:'S1',name:'测试供应商',status:1}],total:1}):undefined)
 const app=mount(Operations,{view:'master'})
 try{await settle();await app.click('编辑 / 停用');const dialog=app.nodes().find(n=>n.props?.role==='dialog');assert.ok(dialog);assert.ok(app.button('保存修改'));assert.ok(!app.button('提交变更审批'));await app.field('名称','修改后的供应商');await app.field('变更原因','更新合作信息');await app.submit();const posts=network.calls.filter(x=>x.options.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].url,'/api/operations/master-changes');assert.equal(posts[0].body.values.name,'修改后的供应商');assert.equal(posts[0].body.actor,undefined);assert.ok(!app.nodes().some(n=>n.props?.role==='dialog'));assert.match(app.text(),/修改已生效/)}finally{app.unmount();network.restore()}
})


test('HITL purchase confirmation shows real product supplier and Chinese business fields only',async()=>{
 setUser(user('purchaser'));const InteractiveBanner=await component('components/InterruptBanner.vue')
 const network=mockNetwork(url=>url==='/api/operations/suggestions?part_id=1'?response({records:[{id:1,name:'活塞环组件',part_code:'PC2001',supplier_name:'测试供应商'}]}):undefined)
 const args={part_id:1,quantity:20,unit_price:3905,expected_date:'2026-10-09',reason:'缺货',manual:true,request_id:'hidden-key'}
 let resumed;const app=mount(InteractiveBanner,{interruptData:{interrupt_type:'hitl_approval',interrupt_id:'confirm',action_requests:[{name:'purchase_request_create',args}]},onResume:value=>resumed=value})
 try{await settle();const text=app.text();for(const value of ['提交采购申请','活塞环组件','测试供应商','采购数量','¥3,905.00','¥78,100.00','预计交货日期'])assert.ok(text.includes(value),value);for(const value of ['purchase_request_create','expected_date','manual','hidden-key'])assert.ok(!text.includes(value),value);assert.equal(!!app.button('✓ 同意').props.disabled,false);await app.click('✓ 同意');assert.ok(resumed);assert.equal(args.part_id,1);assert.equal(args.unit_price,3905);assert.equal(args.supplier_name,undefined)}finally{app.unmount();network.restore()}
})

test('purchase confirmation lookup failure keeps reject available and offers retry',async()=>{
 setUser(user('purchaser'));const InteractiveBanner=await component('components/InterruptBanner.vue');const network=mockNetwork(url=>url.startsWith('/api/operations/suggestions')?response({},503):undefined)
 const app=mount(InteractiveBanner,{interruptData:{interrupt_type:'hitl_approval',interrupt_id:'failed-confirm',action_requests:[{name:'purchase_request_create',args:{part_id:1,quantity:20,unit_price:2}}]}})
 try{await settle();assert.ok(app.button('✓ 同意').props.disabled);assert.equal(!!app.button('✗ 拒绝').props.disabled,false);assert.ok(app.button('重新查询'));assert.match(app.text(),/暂时无法核实/)}finally{app.unmount();network.restore()}
})


test('new assistant answer has no show-full button and completes automatically; history skips typing',async()=>{
 const Message=await component('components/MessageItem.vue');let completed
 const app=mount(Message,{message:{id:'typing-1',role:'assistant',channel:'answer',content:'这是完整的业务回复。',animate:true},onRenderComplete:id=>completed=id})
 try{assert.ok(!app.button('显示全文'));await new Promise(resolve=>setTimeout(resolve,250));await settle();assert.equal(completed,'typing-1');assert.ok(!app.button('显示全文'));const rendered=app.nodes().find(n=>n.props?.innerHTML?.includes('这是完整的业务回复'));assert.ok(rendered)}finally{app.unmount()}
 const history=mount(Message,{message:{id:'history-1',role:'assistant',channel:'answer',content:'历史业务回复。'}})
 try{await settle();assert.ok(!history.button('显示全文'));assert.ok(history.nodes().some(n=>n.props?.innerHTML?.includes('历史业务回复')))}finally{history.unmount()}
})

test('purchaser confirms start procurement only for reviewed orders and refreshes status',async()=>{
 setUser(user('purchaser'))
 const row={id:22,order_number:'PO22',status:2,total_amount:20,part_names:'测试物料',supplier_names:'测试供应商'}
 const net=mockNetwork((url,options)=>{
  if(url.startsWith('/api/dashboard/orders'))return response({records:[row,{id:23,order_number:'PO23',status:1}],total:2})
  if(url==='/api/operations/receiving/22/start'){assert.equal(options.method,'POST');assert.deepEqual(JSON.parse(options.body),{});row.status=3;return response({id:22})}
 })
 const Dashboard=await component('components/Dashboard.vue'),app=mount(Dashboard,{view:'orders'})
 try{
  await settle();assert.equal(app.nodes().filter(n=>n.tagName==='BUTTON'&&n.text==='开始采购').length,1)
  await app.click('开始采购');assert.ok(app.button('确认开始采购'))
  await app.click('确认开始采购');assert.ok(!app.button('开始采购'));assert.match(app.text(),/采购中/)
  assert.equal(net.calls.filter(c=>c.url==='/api/operations/receiving/22/start').length,1)
 }finally{app.unmount();net.restore();clearSession()}
})
