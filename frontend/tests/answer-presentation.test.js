import test from 'node:test'
import assert from 'node:assert/strict'
import { nextTick } from 'vue'
import { component, mount } from './component-helper.js'

const facts = Array.from({length:39}, (_,i)=>({
  result_id:`r${i}`, title:'供应商查询', kind:'supplier',
  records:[{name:`原始供应商${i}`}], returned_count:1,total:1,complete:true,
  columns:[{key:'name',label:'供应商名称',format:'text'}],
  execution:{outcome:'success',message:'查询成功'},
}))

test('curated report is the answer, 39 raw results stay collapsed until requested',async()=>{
  const Component=await component('components/MessageItem.vue')
  const report='已核验 20 条，14 条符合采购条件。\n\n| 物料 | 缺口 |\n| --- | --- |\n| 侧支架 | 24 |\n\n优先处理侧支架。'
  const app=mount(Component,{message:{id:'a',role:'assistant',content:report,business_results:facts}})
  try {
    await nextTick()
    const markdown=app.nodes().find(n=>n.props?.class==='markdown-renderer')
    assert.match(markdown.props.innerHTML,/<table>/)
    assert.match(markdown.props.innerHTML,/侧支架/)
    assert.doesNotMatch(app.text(),/原始供应商|分析说明/)
    const details=app.nodes().find(n=>n.tagName==='DETAILS')
    assert.ok(details);assert.ok(!details.props.open)
    assert.match(app.text(),/查看查询依据（39项）/)
    details.props.onToggle({target:{open:true}});await nextTick()
    assert.match(app.text(),/原始供应商38/)
  } finally {app.unmount()}
})

test('failure text stays readable outside collapsed execution details',async()=>{
  const Component=await component('components/MessageItem.vue')
  const message='当前账号无执行权限，请联系审批人执行。'
  const app=mount(Component,{message:{id:'a',role:'assistant',content:message,wasStreamed:true,animate:true,business_results:[{
    ...facts[0],kind:'master_change',title:'资料变更',records:[],columns:[],execution:{outcome:'failure',message},
  }]}})
  try {
    await nextTick()
    assert.match(app.nodes().find(n=>n.props?.class==='markdown-renderer').props.innerHTML,/当前账号无执行权限/)
    assert.equal(app.nodes().filter(n=>n.tagName==='TABLE').length,0)
  } finally {app.unmount()}
})

test('verified purchase application appears as the submitted record while query evidence stays folded',async()=>{
  const Component=await component('components/MessageItem.vue')
  const application={result_id:'write-3',kind:'purchase_request',title:'采购申请',
    records:[{request_number:'3',part_code:'FC3006',quantity:24,status_name:'待审批'}],
    total:1,returned_count:1,complete:true,
    columns:[{key:'request_number',label:'申请编号'},{key:'part_code',label:'物料编号'},
      {key:'quantity',label:'申请数量',format:'number'},{key:'status_name',label:'申请状态'}],
    execution:{outcome:'success',message:'业务结果已查询核验'}}
  const app=mount(Component,{message:{id:'a',role:'assistant',content:'侧支架补货申请已提交，等待审批。',
    business_results:[...facts.slice(0,2),application]}})
  try {
    await nextTick()
    assert.match(app.text(),/采购申请|申请编号|FC3006|24|待审批/)
    assert.doesNotMatch(app.text(),/原始供应商/)
    const details=app.nodes().find(n=>n.tagName==='DETAILS')
    assert.ok(details);assert.match(app.text(),/查看查询依据（2项）/)
    assert.equal(app.nodes().filter(n=>n.tagName==='TABLE').length,1)
    details.props.onToggle({target:{open:true}});await nextTick()
    assert.match(app.text(),/原始供应商1/)
  }finally{app.unmount()}
})

test('unverified write and failed write do not get a submitted-record card',async()=>{
  const Component=await component('components/MessageItem.vue')
  for(const outcome of ['unknown','failure']){
    const app=mount(Component,{message:{id:outcome,role:'assistant',content:'申请尚未核实。',business_results:[{
      result_id:'unconfirmed',kind:'purchase_request',title:'采购申请',
      records:[{request_number:'999',part_code:'FC3006'}],
      columns:[{key:'request_number',label:'申请编号'}],execution:{outcome,message:'结果未核实'},
    }]}})
    try{await nextTick();assert.doesNotMatch(app.text(),/999/);assert.equal(app.nodes().filter(n=>n.tagName==='TABLE').length,0)}
    finally{app.unmount()}
  }
})
