<template>
<section class="operations">
 <PurchaseOrderForm v-if="purchasePart" :initial-part="purchasePart" @close="closePurchase" @saved="purchaseSaved"/>
 <header><div><p class="eyebrow">风驰 · 采购库存工作台</p><h1>{{ titles[view] }}</h1></div><button @click="refresh" :disabled="busy">刷新</button></header>
 <p class="notice">当前操作身份：{{ identity }}。提交和审批记录使用您的登录身份。</p>
 <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="success" class="success" role="status">{{ success }}</p>
 <p v-if="loading">正在读取数据…</p>
 <div v-if="view!=='stock'" class="filters">
  <label v-if="view==='master'">数据类型<select v-model="entity" @change="reset"><option value="supplier">供应商</option><option value="part">物料</option></select></label>
  <label v-if="view!=='stock'">关键词<input v-model="query" :placeholder="view==='receiving'?'物料名称、物料编码或订单编号':'名称或编号'" @keyup.enter="reset"/></label>
  <label v-if="view==='requests'">额外需求量<input v-model.number="demand" type="number" min="0"/></label>
  <button @click="reset" :disabled="busy">查询</button>
  <button v-if="view==='master' && can('master')" @click="editMaster(null,$event)" :disabled="busy">新增{{ entity==='supplier'?'供应商':'物料' }}</button>
 </div>
 <p v-if="view==='stock' && materialsError" class="error" role="alert">{{ materialsError }} <button @click="loadMaterials" :disabled="materialsLoading || busy">重试物料加载</button></p>
 <section class="panel" v-if="view==='receiving'">
  <p class="note">历史已入库订单保留完成状态；本次升级前的实收明细不作推测。拒收只留记录，少收数量保留待后续到货。</p>
  <OpsTable :rows="receivingRows" :columns="[['order_number','订单编号'],['part_names','物料名称'],['models','型号'],['supplier_names','供应商'],['status','订单状态'],['receipt_state','到货状态'],['accepted','本系统累计验收'],['rejected','累计拒收'],['received_times','实际验收入库时间（北京时间）'],['expected_delivery_date','预计交货']]">
   <template #actions="{row}"><button @click="openReceipt(row.id,$event)" :disabled="busy">明细 / 收货</button></template>
  </OpsTable>
 </section>
 <section class="panel" v-if="view==='stock'">
  <h2 v-if="can('warehouse')">库存作业</h2><form v-if="can('warehouse')" class="form-grid stock-form" @submit.prevent="submitStock">
   <label>操作类型<select v-model="stockForm.kind"><option value="issue">领料出库</option><option value="return">退库</option><option value="count">盘点调整 / 建账</option></select></label>
   <MaterialPicker v-model="stockForm.part_id" :items="materials" label="作业物料" required :loading="materialsLoading" :disabled="busy || !!materialsError"/>
   <label v-if="stockForm.kind==='issue'">领料人姓名<input v-model="stockForm.recipient_name" name="recipient_name" placeholder="请输入实际领料人的姓名" maxlength="100" required/></label>
   <label>{{ stockForm.kind==='count'?'盘点实存数量':'操作数量' }}<input v-model.number="stockForm.quantity" type="number" :min="stockForm.kind==='count'?0:1" :max="stockForm.kind==='issue'?(currentStock ?? 0):undefined" :aria-invalid="!!issueQuantityError" required/><small v-if="stockForm.kind==='issue' && selectedStockPart">当前库存：{{ currentStock ?? '未知' }}{{ selectedStockPart.unit || '' }}</small></label>
   <label v-if="stockForm.kind==='count'">核对时账面数量（建账填0）<input v-model.number="stockForm.expected_quantity" type="number" min="0" required/></label>
   <label>关联单据号<input v-model="stockForm.document" maxlength="128" required/></label>
   <label>操作人<input :value="identity" readonly maxlength="100" required/></label>
   <label class="wide">操作原因<input v-model="stockForm.reason" maxlength="1000" required/></label>
   <p v-if="issueQuantityError" class="wide error" role="alert">{{ issueQuantityError }}</p>
   <button :disabled="busy || materialsLoading || !!materialsError || !!issueQuantityError">确认提交库存变更</button>
  </form><h2>库存流水</h2>
  <OpsTable :rows="rows" :columns="[['id','流水号'],['part_code','物料编号'],['name','物料'],['kind','类型'],['before_quantity','变更前'],['delta','变化量'],['after_quantity','变更后'],['document','关联单据'],['reason','原因'],['recipient_name','领料人'],['actor','操作人'],['created_at','时间']]"><template #actions="{row}"><button v-if="/PO-(\d+)/.test(row.document)" @click="openReceipt(Number(row.document.match(/PO-(\d+)/)[1]))" :disabled="busy">关联订单与收货单</button></template></OpsTable>
  <p class="note">流水从本次升级开始记录。历史库存不生成推测流水；原 Agent 调整入口标记为 legacy-agent。</p>
 </section>
 <section class="panel" v-if="view==='master'">
  <p class="note">审批人和管理员编辑现有主数据后直接生效，并保留修改记录；新增资料仍提交审批。物料停用后禁止新增采购和领料，历史订单仍可收货。</p>
  <OpsTable :rows="rows.map(x=>({...x,cooperation:({1:'合作中',2:'暂停合作',3:'已终止'})[x.status]}))" :columns="entity==='supplier'?[['id','ID'],['supplier_code','编号'],['name','名称'],['cooperation','合作状态'],['contact_person','联系人']]:[['id','ID'],['part_code','编号'],['name','名称'],['supplier_id','供应商ID'],['purchase_price','采购价'],['stock_warning_value','预警值'],['enabled','启用状态']]">
   <template #actions="{row}"><button v-if="can('master')" @click="editMaster(row,$event)" :disabled="busy">编辑 / 停用</button></template>
  </OpsTable>
 </section>
 <section class="panel" v-if="view==='requests'">
  <p class="note">建议采购量 = max(库存预警值 + 额外需求 − 当前库存 − 未到货订单 − 待审批申请, 0)。未审核订单也计入在途以避免重复申请；未知库存需先建账。拒收数量不算已到货。</p>
  <OpsTable :rows="rows" :center-columns="['current_quantity','stock_warning_value','demand','on_order','pending_quantity','suggested']" :columns="[['part_code','物料编号'],['name','物料'],['supplier_name','供应商'],['current_quantity','当前库存'],['stock_warning_value','预警值'],['demand','额外需求'],['on_order','未到货'],['pending_quantity','待审批量'],['suggested','建议采购']]">
   <template #actions="{row}"><button v-if="can('procurement')" @click="openPurchase(row,$event)" :disabled="busy||!row.enabled||row.supplier_status!==1">采购</button><button v-if="can('procurement') && can('chat')" @click="$emit('ask-agent',row)" :disabled="agentBusy">咨询 Agent</button></template>
  </OpsTable>
 </section>
 <footer class="pager"><span>共 {{ total }} 条 · 第 {{ current }} 页</span><button @click="current--;load()" :disabled="busy||current<=1">上一页</button><button @click="current++;load()" :disabled="busy||current*20>=total">下一页</button></footer>

 <div v-if="receipt" class="receipt-overlay" @click.self="closeReceipt" @keydown.esc.stop.prevent="closeReceipt"><section class="receipt-dialog" ref="receiptDialog" role="dialog" aria-modal="true" aria-labelledby="receipt-title" tabindex="-1" @keydown.tab="trapReceiptFocus">
  <header><h2 id="receipt-title">收货：{{ receipt.order_number }}</h2><button @click="closeReceipt" :disabled="busy">关闭</button></header>
  <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="success" class="success" role="status">{{ success }}</p>
  <p>订单金额：{{ receipt.total_amount }}；预计交货：{{ receipt.expected_delivery_date || '未填写' }}；到货状态：{{ display(receipt.receipt_state) }}；仅已审核、采购中订单可提交。</p>
  <p v-if="receipt.review">审核记录：{{ receipt.review.actor }} · {{ receipt.review.decision==='approve'?'通过':'驳回' }} · {{ receipt.review.reason }} · {{ receipt.review.created_at }}</p>
  <p v-if="orderReviewError" class="error" role="alert">{{ orderReviewError }}</p>
  <form v-if="receipt.status===1 && canReview(auth.user,receipt)" class="form-grid" @submit.prevent="reviewOrder">
   <label>订单审核人<input :value="identity" readonly required maxlength="100"/></label><label>审核决定<select v-model="decision"><option value="approve">通过</option><option value="reject">驳回并取消</option></select></label><label>审核意见<input v-model="reviewReason" required maxlength="1000"/></label><button :disabled="busy">确认审核订单</button>
  </form>
  <p v-if="receipt.status===1 && approvalBlock(auth.user,receipt)">{{ approvalBlock(auth.user,receipt) }}</p>
  <form v-if="can('warehouse')" @submit.prevent="submitReceipt">
   <div class="table-scroll"><table><thead><tr><th>物料</th><th>型号</th><th>供应商</th><th>订购</th><th>单价</th><th>小计</th><th>已验收</th><th>剩余</th><th>本批验收</th><th>本批拒收</th></tr></thead><tbody><tr v-for="line in receipt.lines" :key="line.id"><td>{{ line.name }} / {{ line.part_code }}</td><td>{{ line.model || "未维护" }}</td><td>{{ line.supplier_name || "未维护" }}</td><td>{{ line.quantity }}</td><td>{{ line.unit_price }}</td><td>{{ (line.quantity*line.unit_price).toFixed(2) }}</td><td>{{ line.accepted }}</td><td>{{ line.remaining }}</td><td><input type="number" min="0" :max="line.remaining" v-model.number="line.receive"/></td><td><input type="number" min="0" :max="line.remaining" v-model.number="line.reject"/></td></tr></tbody></table></div>
   <div class="form-grid"><label>本批送货单号（同订单不可重复）<input v-model="deliveryNumber" name="delivery_number" autocomplete="off" placeholder="请输入供应商送货单上的编号" required maxlength="128"/></label><label>收货人<input :value="identity" readonly required maxlength="100"/></label><label>收货 / 拒收说明<input v-model="receiptReason" name="receipt_reason" autocomplete="off" placeholder="请输入本批验收或拒收的情况" required maxlength="1000"/></label><button :disabled="busy||![2,3].includes(receipt.status)">确认本批收货并入库</button></div>
  </form>
  <h3>收货记录</h3><OpsTable :rows="receipt.receipts.map(r=>({...r,id:`${r.id}-${r.detail_id}`,order_number:receipt.order_number}))" :columns="[['order_number','订单编号'],['delivery_number','送货单号'],['detail_id','订单行'],['accepted','验收数量'],['rejected','拒收数量'],['reason','说明'],['actor','收货人'],['created_at','时间']]"/>
 </section></div>
 <div v-if="masterForm" class="receipt-overlay" @click.self="closeMaster" @keydown.esc.stop.prevent="closeMaster"><section class="receipt-dialog master-dialog" ref="masterDialog" role="dialog" aria-modal="true" aria-labelledby="master-title" tabindex="-1" @keydown.tab="trapMasterFocus">
  <header><h2 id="master-title">{{ masterId?'编辑':'新增' }}{{ entity==='supplier'?'供应商':'物料' }}</h2><button @click="closeMaster" :disabled="busy">关闭</button></header><p class="note">{{ masterDirect?'保存后立即生效，并记录修改前后内容和您的操作身份。':'提交后等待审批，通过后生效。' }}</p><p v-if="error" class="error" role="alert">{{ error }}</p>
  <form class="form-grid" @submit.prevent="submitMaster">
   <label v-for="field in masterFields" :key="field.key">{{ labels[field.key] }}
    <select v-if="field.key==='status'" v-model.number="masterForm.status"><option :value="1">合作中</option><option :value="2">暂停合作</option><option :value="3">已终止</option></select>
    <select v-else-if="field.key==='enabled'" v-model="masterForm.enabled"><option :value="true">启用</option><option :value="false">停用</option></select>
    <select v-else-if="field.key==='credit_rating'" v-model="masterForm.credit_rating"><option v-for="r in ['A','B','C','D']" :key="r">{{ r }}</option></select>
    <input v-else v-model="masterForm[field.key]" :type="field.type||'text'" :step="field.key==='purchase_price'?'0.01':'1'" :min="field.key==='supplier_id'?1:0" :required="field.required" :maxlength="field.max||100"/>
   </label>
   <label>提交人<input :value="identity" readonly required maxlength="100"/></label><label class="wide">变更原因<input v-model="masterReason" required maxlength="1000"/></label><button :disabled="busy">{{ masterDirect?'保存修改':'提交变更审批' }}</button>
  </form>
 </section></div>
 <section v-if="requestForm && can('procurement')" class="panel" ref="editor">
  <header><h2>采购申请：{{ requestForm.name }}（{{ requestForm.part_code }}）</h2><button @click="requestForm=null" :disabled="busy">关闭</button></header>
  <form class="form-grid" @submit.prevent="submitRequest">
   <label>采购数量<input v-model.number="requestForm.quantity" type="number" min="1" required/></label><label>单价<input v-model.number="requestForm.unit_price" type="number" min="0" step="0.01" required/></label>
   <label>预计交货日期<input v-model="requestForm.expected_date" type="date" required/></label><label>额外需求量<input v-model.number="requestForm.demand" type="number" min="0" required/></label><label>申请人<input :value="identity" readonly required maxlength="100"/></label><label class="wide">申请原因<input v-model="requestForm.reason" required maxlength="1000"/></label><button :disabled="busy">提交采购申请</button>
  </form>
 </section>
 <section v-if="['master','requests'].includes(view)" class="panel">
  <h2>{{ view==='master'?'主数据修改记录':'采购申请审批与记录' }}</h2>
  <OpsTable :rows="approvals" :columns="view==='master'?[['id','记录编号'],['entity','资料类型'],['entity_id','主数据ID'],['status','状态'],['actor','操作人'],['reason','变更原因'],['created_at','修改时间']]:[['id','申请号'],['name','物料'],['quantity','数量'],['unit_price','单价'],['expected_date','交货日期'],['status','状态'],['order_id','生成订单ID'],['actor','申请人'],['reviewer','审批人']]">
   <template #actions="{row}"><button @click="openReview(row)" :disabled="busy">{{ row.status==='pending' && canReview(auth.user,row)?'查看并审批':'查看记录' }}</button></template>
  </OpsTable>
  <footer class="pager"><span>共 {{ approvalTotal }} 条 · 第 {{ approvalPage }} 页</span><button @click="approvalPage--;loadApprovals()" :disabled="busy||approvalPage<=1">上一页</button><button @click="approvalPage++;loadApprovals()" :disabled="busy||approvalPage*20>=approvalTotal">下一页</button></footer>
 </section>
 <section v-if="reviewForm" class="panel review" ref="reviewPanel">
  <p v-if="approvalError" class="error" role="alert">{{ approvalError }}</p>
  <header><h2>{{ reviewForm.status==='applied'?'修改记录':'申请' }} #{{ reviewForm.id }} · {{ display(reviewForm.status) }}</h2><button @click="reviewForm=null" :disabled="busy">关闭</button></header>
  <p>提交人：{{ reviewForm.actor }}；申请原因：{{ reviewForm.reason }}</p>
  <OpsTable v-if="view==='master'" :rows="changeRows(reviewForm.previous,reviewForm.proposed)" :columns="[['field','字段'],['before','变更前'],['after',reviewForm.status==='applied'?'修改后':'申请值']]"/>
  <p v-else>物料：{{ reviewForm.name }}；供应商：{{ reviewForm.supplier_name }}；数量：{{ reviewForm.quantity }}；单价：{{ reviewForm.unit_price }}；交货日期：{{ reviewForm.expected_date }}；额外需求：{{ reviewForm.demand }}；订单ID：{{ reviewForm.order_id || '未生成' }}</p>
  <form v-if="reviewForm.status==='pending' && canReview(auth.user,reviewForm)" class="form-grid" @submit.prevent="submitReview">
   <label>审批人<input :value="identity" readonly required maxlength="100"/></label><label>决定<select v-model="decision"><option value="approve">通过</option><option value="reject">驳回</option></select></label><label class="wide">审批意见<input v-model="reviewReason" required maxlength="1000"/></label><button :disabled="busy">确认审批{{ view==='requests'&&decision==='approve'?'并生成订单':'' }}</button>
  </form><p v-else-if="reviewForm.status==='pending'">{{ approvalBlock(auth.user,reviewForm) }}</p><p v-else-if="reviewForm.status==='applied'">修改已生效，未经过审批。修改人：{{ reviewForm.actor }}；生效时间：{{ reviewForm.reviewed_at || reviewForm.created_at }}</p><p v-else>审批人：{{ reviewForm.reviewer }}；审批意见：{{ reviewForm.review_reason }}；审批时间：{{ reviewForm.reviewed_at }}</p>
 </section>
</section>
</template>
<script setup>
import { apiFetch as fetch } from '../api/http.js'

import {ref,computed,watch,nextTick,onBeforeUnmount} from 'vue'
import { auth, identity, can, userStorageKey } from '../auth/state.js'
import { canReview, approvalBlock } from '../auth/permissions.js'
import OpsTable from './OpsTable.vue'
import PurchaseOrderForm from './PurchaseOrderForm.vue'
import MaterialPicker from './MaterialPicker.vue'
import { allMaterials } from '../api/materials.js'
import {labels,display,changeRows,receiptPayload,masterValues,operationKey,businessError} from '../utils/operations.js'
const props=defineProps({view:String,initialPart:Number,agentBusy:Boolean});defineEmits(['ask-agent'])
const titles={receiving:'入库与收货',stock:'库存作业与流水',master:'主数据维护',requests:'采购申请与补货建议'}
const rows=ref([]),total=ref(0),current=ref(1),query=ref(''),entity=ref('supplier'),demand=ref(0),loading=ref(false),busy=ref(false),error=ref(''),success=ref(''),receipt=ref(null),receiptReason=ref(''),deliveryNumber=ref(''),masterForm=ref(null),masterId=ref(null),masterReason=ref(''),requestForm=ref(null),approvals=ref([]),approvalTotal=ref(0),approvalPage=ref(1),reviewForm=ref(null),reviewReason=ref(''),decision=ref('approve'),editor=ref(null)
const orderReviewError=ref(''),approvalError=ref(''),reviewPanel=ref(null)
const receivingRows=computed(()=>rows.value.map(row=>({...row,received_times:receiptTimes(row)})))
function receiptTimes(row){
 if(!row.receipt_items?.length)return '—'
 return row.receipt_items.map(item=>{
  const material=[item.name,item.part_code].filter(Boolean).join(' / ') || '—'
  const batches=(item.accepted_batches||[]).filter(batch=>batch.accepted_quantity>0)
  if(!batches.length)return material+'：—'
  return batches.map(batch=>material+'：'+(batch.received_at?.replace('T',' ').replace(/\+08:00$/,'') || '—')+'（验收 '+batch.accepted_quantity+(item.unit||'')+'；收货单 '+batch.delivery_number+'）').join('\n')
 }).join('\n')
}
async function openReview(row){reviewForm.value={...row};reviewReason.value='';decision.value='approve';approvalError.value='';await nextTick();reviewPanel.value?.scrollIntoView({behavior:'smooth',block:'start'})}
const operationStorageKey=userStorageKey('erp.pending-operation')
let version=0,alive=true,requestKey=null,lastPayload=null
const materials=ref([]),materialsLoading=ref(false),materialsError=ref('')
let materialsController
async function loadMaterials(){
 materialsController?.abort()
 const controller=new AbortController();materialsController=controller;materialsLoading.value=true;materialsError.value=''
 try{const data=await allMaterials(controller.signal);if(alive && !controller.signal.aborted)materials.value=data}
 catch(e){if(alive && !controller.signal.aborted && e.name!=='AbortError')materialsError.value='物料加载失败，请您重试。'}
 finally{if(alive && materialsController===controller)materialsLoading.value=false}
}
function refresh(){load();if(props.view==='stock')loadMaterials()}
const masterFields=computed(()=>entity.value==='supplier'?[{key:'supplier_code',required:true,max:50},{key:'name',required:true},{key:'status'},{key:'credit_rating'},{key:'contact_person',max:50},{key:'phone',max:20},{key:'email'},{key:'address',max:200}]:[{key:'part_code',required:true,max:50},{key:'name',required:true},{key:'supplier_id',type:'number',required:true},{key:'purchase_price',type:'number',required:true},{key:'stock_warning_value',type:'number',required:true},{key:'enabled'},{key:'model'},{key:'unit',required:true,max:20},{key:'category',max:50},{key:'specification',max:200}])
async function api(path,body){const response=await fetch('/api/operations/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);let data;try{data=await response.json()}catch{throw Error('服务暂不可用，请保留当前表单稍后重试')}if(!response.ok)throw Error(businessError(data.detail));return data}
async function load(){if(!alive||!can(({receiving:'warehouse',stock:'warehouse',master:'master',requests:'read_procurement'})[props.view]))return;const ticket=++version;loading.value=true;error.value='';try{let path;const params=new URLSearchParams({current:current.value,size:20,q:query.value});if(props.view==='receiving')path='receiving';if(props.view==='stock'){path='movements';params.delete('q')}if(props.view==='master')path='master/'+entity.value;if(props.view==='requests'){path='suggestions';params.set('demand',demand.value);if(props.initialPart)params.set('part_id',props.initialPart)}const data=await api(path+'?'+params);if(!alive||ticket!==version)return;rows.value=data.records;total.value=data.total;if(['master','requests'].includes(props.view))await loadApprovals()}catch(e){if(ticket===version)error.value=e.message}finally{if(ticket===version)loading.value=false}}
async function loadApprovals(){const view=props.view;try{const data=await api((view==='master'?'master-changes':'requests')+'?current='+approvalPage.value);if(alive&&props.view===view){approvals.value=data.records;approvalTotal.value=data.total}}catch(e){error.value=e.message}}
function reset(){receiptVersion++;current.value=1;receipt.value=null;masterForm.value=null;requestForm.value=null;reviewForm.value=null;load()}
async function scroll(){await nextTick();editor.value?.scrollIntoView({behavior:'smooth',block:'start'})}
const receiptDialog=ref(null)
let receiptOpener=null,receiptVersion=0
function closeReceipt(){if(busy.value)return;receiptVersion++;receipt.value=null;nextTick(()=>receiptOpener?.focus?.())}
function trapReceiptFocus(event){const nodes=receiptDialog.value?.querySelectorAll?.('button:not(:disabled),input:not(:disabled),select:not(:disabled)');if(!nodes?.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&(document.activeElement===first||document.activeElement===receiptDialog.value)){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
async function openReceipt(id,event){if(event)receiptOpener=event.currentTarget;const ticket=++receiptVersion;orderReviewError.value='';error.value='';try{const data=await api('receiving/'+id);if(!alive||ticket!==receiptVersion)return;receipt.value=data;receipt.value.lines.forEach(x=>{x.receive=0;x.reject=0});receiptReason.value='';deliveryNumber.value='';await nextTick();receiptDialog.value?.focus?.()}catch(e){if(alive&&ticket===receiptVersion)error.value=e.message}}
const masterDialog=ref(null)
const masterDirect=computed(()=>!!masterId.value&&['approver','admin'].includes(auth.user?.role))
let masterOpener=null
function closeMaster(){if(busy.value)return;masterForm.value=null;nextTick(()=>masterOpener?.focus?.())}
function trapMasterFocus(event){const nodes=masterDialog.value?.querySelectorAll?.('button:not(:disabled),input:not(:disabled),select:not(:disabled)');if(!nodes?.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&(document.activeElement===first||document.activeElement===masterDialog.value)){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
function editMaster(row,event){if(!can('master'))return;masterOpener=event?.currentTarget;error.value='';masterId.value=row?.id||null;masterForm.value={};for(const f of masterFields.value)masterForm.value[f.key]=row?.[f.key]??({status:1,credit_rating:'B',enabled:true,purchase_price:0,stock_warning_value:10,unit:'个'}[f.key]??'');if(entity.value==='part')masterForm.value.enabled=Boolean(masterForm.value.enabled);masterReason.value='';nextTick(()=>masterDialog.value?.focus?.())}
const purchasePart=ref(null)
let purchaseOpener=null

function openPurchase(row,event){if(!can('procurement')||busy.value||!row.enabled||row.supplier_status!==1)return;purchaseOpener=event?.currentTarget;purchasePart.value={...row};success.value=''}
function closePurchase(){purchasePart.value=null;nextTick(()=>purchaseOpener?.focus?.())}
async function purchaseSaved(){closePurchase();success.value='采购订单已创建，状态为待审核，请由其他审批人审核。';await load()}
function openRequest(row){if(!can('procurement'))return;requestForm.value={...row,part_id:row.id,quantity:row.suggested,unit_price:row.purchase_price,expected_date:'',reason:'',demand:demand.value};scroll()}
async function mutate(path,body,close){
 if(busy.value)return
 busy.value=true;error.value='';success.value=''
 try{
  const signature=JSON.stringify({path,body})
  if(signature!==lastPayload){
   let saved;try{saved=JSON.parse(sessionStorage.getItem(operationStorageKey)||'null')}catch{}
   requestKey=saved?.signature===signature?saved.key:operationKey();lastPayload=signature
   try{sessionStorage.setItem(operationStorageKey,JSON.stringify({signature,key:requestKey}))}catch{}
  }
  const result=await api(path,{...body,request_id:requestKey})
  requestKey=null;lastPayload=null;try{sessionStorage.removeItem(operationStorageKey)}catch{}
  close?.();await load();success.value=result.status==='applied'?'主数据修改已生效，修改记录已保存。':'已提交成功'+(result.id?'，单据 #'+result.id:'')
 }catch(e){error.value=e.message}finally{busy.value=false}
}
async function reviewOrder(){if(busy.value||!canReview(auth.user,receipt.value))return;busy.value=true;error.value='';orderReviewError.value='';const id=receipt.value.id;try{await api('receiving/'+id+'/review',{decision:decision.value,reason:reviewReason.value,expected_version:receipt.value.review_version});await load();await openReceipt(id);success.value='订单审核已记录'}catch(e){orderReviewError.value=e.message}finally{busy.value=false}}
async function submitReceipt(){if(!can('warehouse'))return;const lines=receiptPayload(receipt.value.lines);if(!lines.length){error.value='请填写本批验收或拒收数量';return}const id=receipt.value.id;await mutate('receipts',{order_id:id,delivery_number:deliveryNumber.value,reason:receiptReason.value,lines},()=>{receipt.value=null});if(!error.value)await openReceipt(id)}
async function submitStock(){
 if(!can('warehouse')||busy.value||materialsLoading.value||materialsError.value)return
 if(!selectedStockPart.value){error.value='请您选择需要操作的物料。';return}
 if(issueQuantityError.value)return
 const data={...stockForm.value,part_id:Number(stockForm.value.part_id)}
 if(data.kind==='issue'){
  data.recipient_name=String(data.recipient_name||'').trim()
  if(!data.recipient_name){error.value='请您填写领料人姓名。';return}
 }else delete data.recipient_name
 if(data.kind!=='count')delete data.expected_quantity
 await mutate('stock',data,()=>{stockForm.value.reason='';stockForm.value.quantity=0;stockForm.value.recipient_name=''})
 // Reload after success or rejection: another operator may have changed stock.
 if(alive)await loadMaterials()
}
const stockForm=ref({part_id:'',kind:'issue',quantity:0,expected_quantity:0,document:'',reason:'',recipient_name:''})
const selectedStockPart=computed(()=>materials.value.find(part=>Number(part.id)===Number(stockForm.value.part_id)))
const currentStock=computed(()=>{
 const quantity=selectedStockPart.value?.quantity
 if(quantity==null || quantity==='' || typeof quantity==='boolean')return null
 const value=Number(quantity)
 return Number.isSafeInteger(value)&&value>=0?value:null
})
const issueQuantityError=computed(()=>{
 if(stockForm.value.kind!=='issue'||!selectedStockPart.value)return ''
 if(currentStock.value===null)return '当前库存尚未核实，请刷新物料后再领料；未建账物料请先盘点建账。'
 if(currentStock.value===0)return '当前库存为零，不能领料出库。'
 return Number(stockForm.value.quantity)>currentStock.value?`操作数量不能大于当前库存（${currentStock.value}）。`:''
})
function submitMaster(){if(!can('master'))return;return mutate('master-changes',{entity:entity.value,entity_id:masterId.value,values:masterValues(entity.value,masterForm.value),reason:masterReason.value},()=>{masterForm.value=null;nextTick(()=>masterOpener?.focus?.())})}
function submitRequest(){if(!can('procurement'))return;const f=requestForm.value;return mutate('requests',{part_id:f.part_id,quantity:f.quantity,unit_price:f.unit_price,expected_date:f.expected_date,demand:f.demand,reason:f.reason},()=>{requestForm.value=null})}
async function submitReview(){if(busy.value||!canReview(auth.user,reviewForm.value))return;busy.value=true;error.value='';approvalError.value='';try{const result=await api((props.view==='master'?'master-changes':'requests')+'/'+reviewForm.value.id+'/review',{decision:decision.value,reason:reviewReason.value});reviewForm.value=null;await load();success.value='审批已处理'+(result.order_id?'，已生成订单 #'+result.order_id:'')}catch(e){approvalError.value=e.message}finally{busy.value=false}}
watch(()=>[props.view,props.initialPart],()=>{receiptVersion++;purchasePart.value=null;current.value=1;approvalPage.value=1;query.value='';receipt.value=null;masterForm.value=null;requestForm.value=null;reviewForm.value=null;success.value='';load()},{immediate:true})
watch(()=>props.view,view=>{materialsController?.abort();if(view==='stock' && can('warehouse'))loadMaterials()},{immediate:true})
onBeforeUnmount(()=>{alive=false;version++;receiptVersion++;materialsController?.abort()})
</script>
<style scoped>
.form-grid.stock-form{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));align-items:start}.stock-form :deep(label){min-width:0}.stock-form :deep(input),.stock-form :deep(select){width:100%;height:40px}.stock-form>.wide,.stock-form>button{grid-column:1/-1}.stock-form>button{justify-self:start}@media(max-width:1300px){.stock-form{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:900px){.stock-form{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:700px){.stock-form{grid-template-columns:1fr}}
.master-dialog{width:min(900px,100%)}.receipt-overlay{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:24px;background:#183b3566}.receipt-dialog{box-sizing:border-box;width:min(1200px,100%);max-height:90dvh;overflow:auto;overscroll-behavior:contain;padding:26px;border:1px solid #c7dbce;border-radius:16px;background:#fff;box-shadow:0 20px 70px #173c3533}.receipt-dialog:focus{outline:none}@media(max-width:700px){.receipt-overlay{padding:10px}.receipt-dialog{padding:16px}}

.operations{flex:1;overflow:auto;padding:26px 32px;background:#f5f7f3;color:#243b39;min-width:0}header{display:flex;align-items:center;justify-content:space-between;gap:15px}h1{font-size:28px;margin:8px 0 18px}h2{font-size:18px;margin:18px 0}.eyebrow{font-size:12px;color:#258574}.panel{background:white;border:1px solid #dce8df;border-radius:12px;padding:20px;margin:16px 0}.filters,.form-grid{display:flex;flex-wrap:wrap;align-items:flex-end;gap:14px;margin:14px 0}.form-grid label{flex:1 1 190px}.form-grid .wide{flex-basis:100%}label{display:flex;flex-direction:column;gap:7px;font-size:13px}input,select{padding:9px;border:1px solid #c7dbce;border-radius:6px;font:inherit;color:inherit;background:white;max-width:100%;box-sizing:border-box}button{border:1px solid #bed8cd;background:#eef7f1;color:#237d6d;padding:9px 12px;border-radius:7px;cursor:pointer;font:inherit;font-size:13px;margin-right:6px}button:disabled{opacity:.45;cursor:not-allowed}.notice,.note{font-size:13px;line-height:1.7;color:#617a6d}.error,.success{padding:13px;border-radius:8px;white-space:pre-wrap}.error{background:#fff0e9;color:#a54125}.success{background:#e0f4e6;color:#256e42}.pager{display:flex;justify-content:flex-end;gap:10px;align-items:center;font-size:13px}.pager span{margin-right:auto}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:10px;text-align:center;vertical-align:middle;border-bottom:1px solid #e1ebe3}td input{width:90px;text-align:center}.review{border-color:#76ad94}@media(max-width:700px){.operations{padding:16px}.form-grid label{flex-basis:100%}}
</style>
