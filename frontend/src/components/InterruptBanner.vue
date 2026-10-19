<template>
  <div class="interrupt-banner" :class="bannerClass">
    <template v-if="!canHandleInterrupt(auth.user,interruptData)">
      <div class="interrupt-header"><span class="interrupt-title">操作权限说明</span><button type="button" class="close-supplement" :disabled="submitting" @click="handleClose">关闭</button></div>
      <p class="interrupt-hint">{{ interruptPermissionMessage(interruptData) }}</p>
    </template>
    <!-- ============================================================ -->
    <!-- 类型 1：数据补充中断（request_order_info） -->
    <!-- ============================================================ -->
    <template v-else-if="interruptData.interrupt_type === 'order_info_supplement'">
      <div class="interrupt-header">
        <span class="interrupt-icon">📋</span>
        <span class="interrupt-title">请补充采购所需信息</span>
        <button type="button" class="close-supplement" :disabled="submitting" @click="handleClose">关闭</button>
      </div>

      <p v-if="!canProcure" class="interrupt-hint">您的账号没有采购权限，请关闭此窗口并联系采购员处理。</p>
      <div v-if="canProcure" class="interrupt-body">
        <p v-if="supplementReason" class="interrupt-hint">{{ supplementReason }}</p>
        <div class="info-block missing-fields">
          <div class="info-label">缺少字段</div>
          <div class="info-value">{{ translateFields(interruptData.missing_fields) }}</div>
        </div>
        <div class="info-block collected-data" v-if="interruptData.collected_data">
          <div class="info-label">已收集数据</div>
          <pre class="info-pre">{{ formatCollectedData(interruptData.collected_data) }}</pre>
        </div>
      </div>

      <div v-if="canProcure" class="order-form-area">
        <p v-if="catalogLoading" class="catalog-notice" role="status">正在加载供应商和物料…</p>
        <p v-if="catalogError" class="form-error" role="alert">{{ catalogError }} <button type="button" :disabled="submitting || catalogLoading" @click="loadCatalog">重新加载</button></p>
        <label v-if="interruptData.fields?.length && interruptData.workflow_type !== 'procurement'" class="free-mode">
          <input type="checkbox" v-model="freeTextMode" :disabled="submitting" />
          信息尚不确定，改用自由补充
        </label>
        <div v-if="!freeTextMode" class="order-form">
          <fieldset v-for="group in formGroups" :key="group.index" :disabled="submitting" class="order-group">
            <legend>订单 {{ group.index + 1 }}</legend>
            <div v-for="section in [{ index: null, fields: group.fields }, ...group.details]" :key="section.index ?? 'header'">
              <h4 v-if="section.index !== null">明细 {{ section.index + 1 }}</h4>
              <div class="form-grid">
                <label v-for="field in section.fields" :key="field.path" class="form-field">
                  <span>{{ fieldLabel(field) }}<span v-if="field.required" class="required"> *</span></span>
                  <select v-if="choiceType(field)" :value="formValues[field.path]" @change="chooseField(field,$event.target.value)" :required="field.required" :disabled="catalogLoading || !!catalogError || !catalogReady || (choiceType(field)==='part' && !materialOptions(field).length)" :aria-label="fieldAriaLabel(field,group,section)" :aria-invalid="Boolean(formErrors[field.path])" :aria-describedby="fieldHelpId(field)">
                    <option value="">{{ choiceType(field)==='supplier' ? '请选择合作中的供应商' : '请选择物料' }}</option>
                    <option v-for="row in choiceType(field)==='supplier' ? catalog.suppliers : materialOptions(field)" :key="row.id" :value="String(row.id)">{{ row.name }} · {{ choiceType(field)==='supplier' ? row.supplier_code : row.part_code }}{{ choiceType(field)==='part' && row.model ? ' · '+row.model : '' }}</option>
                  </select>
                  <input
                    v-else
                    :value="formValues[field.path]"
                    @input="formValues[field.path] = $event.target.value"
                    :type="inputType(field)"
                    :min="field.min" :max="field.max"
                    :step="field.type === 'integer' ? 1 : 'any'"
                    :required="field.required"
                    :aria-label="fieldAriaLabel(field,group,section)"
                    :aria-invalid="Boolean(formErrors[field.path])"
                    :aria-describedby="fieldHelpId(field)"
                  />
                  <small :id="fieldHelpId(field)" :class="{ 'form-error': formErrors[field.path] }">
                    {{ formErrors[field.path] || choiceHint(field) || (field.error ? translateFields(field.error) : '') }}
                  </small>
                </label>
              </div>
            </div>
          </fieldset>
        </div>
        <label class="note-label" :for="`supplement-${interruptData.interrupt_id}`">
          {{ freeTextMode ? '补充说明（必填）' : '其他补充说明（可选）' }}
        </label>
        <textarea
          :id="`supplement-${interruptData.interrupt_id}`"
          ref="supplementInputRef"
          v-model="supplementText"
          :disabled="submitting"
          :placeholder="interruptData.workflow_type === 'procurement' ? '请在表单中补齐必填字段；其他说明可在此填写。' : '不确定的供应商、物料、交货方式或更多明细，可在此说明，交由助手继续确认。'"
          class="supplement-textarea form-note"
          rows="3"
        ></textarea>
        <p v-if="submitError" class="form-error" role="alert">{{ submitError }}</p>
        <button class="submit-btn supplement-btn" @click="handleSupplement" :disabled="submitting || (!freeTextMode && needsCatalog && (catalogLoading || !catalogReady || !!catalogError))">
          {{ submitting ? '提交中...' : '提交补充信息' }}
        </button>
      </div>
      <div v-if="canProcure" class="interrupt-hint">补充后助手会请您确认提交。新订单为待审核状态，须由其他审批人正式审核后执行采购。</div>
    </template>

    <!-- ============================================================ -->
    <!-- 类型 2：HITL 审批中断（order_create / order_update） -->
    <!-- ============================================================ -->
    <template v-else-if="interruptData.interrupt_type === 'hitl_approval'">
      <div class="interrupt-header">
        <span class="interrupt-icon">⚠️</span>
        <span class="interrupt-title">需要人工确认以下操作</span>
      </div>

      <div class="interrupt-body">
        <div
          v-for="(action, idx) in interruptData.action_requests"
          :key="idx"
          class="action-card"
        >
          <div class="action-header">
            <span class="action-type">{{ formatActionName(action.name) }}</span>
            <select v-if="!reviewBlock && interruptData.action_requests.length > 1" v-model="choices[idx]" :disabled="submitting" :aria-label="`第 ${idx + 1} 项操作审批`" class="decision-select">
              <option value="" disabled>请选择决定</option>
              <option value="approve">同意</option>
              <option value="reject">拒绝</option>
            </select>
          </div>
          <div class="action-details">
            <p v-if="action.name==='purchase_request_create' && partLookup[idx]?.loading" class="interrupt-hint">正在核实商品和供应商信息…</p>
            <p v-if="action.name==='purchase_request_create' && partLookup[idx]?.error" class="interrupt-hint">{{ partLookup[idx].error }} <button :disabled="submitting" @click="loadPurchaseInfo">重新查询</button></p>
            <table v-if="action.name==='purchase_request_create'" class="order-table"><tbody><tr v-for="row in purchaseConfirmationRows(action.args,partLookup[idx]?.part)" :key="row[0]"><td class="field-label">{{ row[0] }}</td><td class="field-value">{{ row[1] }}</td></tr></tbody></table>
            <table class="order-table" v-else-if="action.args">
              <tbody>
                <template v-for="(val, key) in displayArgs(action.args)" :key="key">
                  <tr v-if="key !== 'orderDetail'">
                    <td class="field-label">{{ formatFieldName(key) }}</td>
                    <td class="field-value">{{ formatFieldValue(key, val) }}</td>
                  </tr>
                </template>
              </tbody>
            </table>
            <!-- 订单明细子表 -->
            <div v-if="orderDetails(action.args).length" class="detail-table-wrap">
              <div class="detail-label">订单明细 ({{ orderDetails(action.args).length }} 项)</div>
              <table class="detail-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>物料ID</th>
                    <th>数量</th>
                    <th>单价</th>
                    <th>小计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(item, di) in orderDetails(action.args)" :key="di">
                    <td>{{ di + 1 }}</td>
                    <td>{{ item.partId || '-' }}</td>
                    <td>{{ item.quantity || '-' }}</td>
                    <td>{{ item.unitPrice != null ? item.unitPrice : '-' }}</td>
                    <td>{{ detailSubtotal(item) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <p class="interrupt-hint">{{ interruptData.action_requests.every(action=>action.name==='purchase_request_create')?'确认后提交采购申请，须由其他审批人审批通过后生成订单。':'确认后提交业务操作；新订单为待审核状态，须由其他审批人正式审批。' }}</p>
      <p v-if="reviewBlock" class="interrupt-hint">{{ reviewBlock }}</p>
      <div v-else class="interrupt-actions">
        <button v-if="interruptData.action_requests.length > 1" class="submit-btn supplement-btn" :disabled="submitting || !allChosen || purchaseInfoPending" @click="handleMixedDecision">提交逐项决定</button>
        <button
          class="submit-btn approve-btn"
          @click="handleDecision('approve')"
          :disabled="submitting || purchaseInfoPending"
        >
          {{ submitting ? '处理中...' : (interruptData.action_requests.length > 1 ? '全部同意' : '✓ 同意') }}
        </button>
        <button
          class="submit-btn reject-btn"
          @click="handleDecision('reject')"
          :disabled="submitting"
        >
          {{ submitting ? '处理中...' : (interruptData.action_requests.length > 1 ? '全部拒绝' : '✗ 拒绝') }}
        </button>
      </div>
    </template>

    <!-- ============================================================ -->
    <!-- 工作流恢复 -->
    <!-- ============================================================ -->
    <template v-else-if="interruptData.interrupt_type === 'workflow_recovery'">
      <div class="interrupt-header">
        <span class="interrupt-icon">⏸️</span>
        <span class="interrupt-title">{{ recoveryTitle }}</span>
        <button v-if="!canProcure" type="button" class="close-supplement" :disabled="submitting" @click="handleClose">关闭</button>
      </div>
      <div class="interrupt-body"><p class="interrupt-hint">{{ canProcure ? recoveryMessage : '您的账号没有采购权限，请关闭此窗口并联系采购员处理。' }}</p></div>
      <div v-if="canProcure" class="interrupt-actions">
        <button class="submit-btn supplement-btn" :disabled="submitting" @click="handleRecovery('retry')">重新核验</button>
        <button class="submit-btn recovery-cancel-btn" :disabled="submitting" @click="handleRecovery('cancel')">取消本次操作</button>
      </div>
    </template>
    <template v-else>
      <div class="interrupt-header">
        <span class="interrupt-icon">⏸️</span>
        <span class="interrupt-title">助手已暂停，等待您确认</span>
      </div>
      <div class="interrupt-body">
        <p class="interrupt-hint">{{ recoveryMessage }}</p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { auth } from '../auth/state.js'
import { hasPermission } from '../auth/permissions.js'
import { formatFieldName, formatFieldValue, displayArgs, orderDetails, detailSubtotal, translateFields, formatCollectedData, purchaseConfirmationRows } from '../utils/approval-display.js'
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import {procurementOptions} from '../api/procurement-options.js'
import { apiFetch } from '../api/http.js'
import { approvalResponse, canHandleInterrupt, interruptPermissionMessage } from '../utils/approval.js'
import { initialOrderValues, orderFormGroups, orderFormErrors, orderFormResponse } from '../utils/order-form.js'
import { businessCopy } from '../utils/answer-stream.js'

const props = defineProps({
  interruptData: { type: Object, required: true },
  submitting: { type: Boolean, default: false }
})
const supplementReason = computed(() => businessCopy(props.interruptData.reason))
const canProcure = computed(() => hasPermission(auth.user, 'procurement'))
const recoveryTitle = computed(() => businessCopy(props.interruptData.title, '本次核验未完成'))
const recoveryMessage = computed(() => businessCopy(props.interruptData.message || props.interruptData.result_message || props.interruptData.result?.execution?.message || props.interruptData.execution?.message, '请您重新核验，或取消本次操作。'))
function handleRecovery(action) {
  if (props.submitting || !canProcure.value || !props.interruptData.interrupt_id) return
  emit('resume', {interrupt_id:props.interruptData.interrupt_id, workflow_action:action})
}

const partLookup=ref({})
let lookupVersion=0,lookupController=null
async function loadPurchaseInfo(){
 const ticket=++lookupVersion;lookupController?.abort();lookupController=new AbortController();const signal=lookupController.signal
 const entries=(props.interruptData.action_requests || []).map((action,index)=>({action,index})).filter(({action})=>canHandleInterrupt(auth.user,props.interruptData) && action.name==='purchase_request_create')
 partLookup.value=Object.fromEntries(entries.map(({index})=>[index,{loading:true,part:null,error:''}]))
 await Promise.all(entries.map(async({action,index})=>{
  try{
   const id=Number(action.args?.part_id);if(!Number.isInteger(id)||id<1)throw Error('物料编号缺失，请先补充物料信息。')
   const response=await apiFetch('/api/operations/suggestions?part_id='+id,{signal});if(!response.ok)throw Error('商品和供应商信息暂时无法核实，请重新查询。')
   const data=await response.json();const part=data.records?.find(row=>Number(row.id)===id)
   if(!part?.name||!part?.supplier_name)throw Error('未查询到完整的商品和供应商信息，请核对物料资料。')
   if(ticket===lookupVersion)partLookup.value[index]={loading:false,part,error:''}
  }catch(error){if(ticket===lookupVersion&&!signal.aborted)partLookup.value[index]={loading:false,part:null,error:error.message}}
 }))
}
const purchaseInfoPending=computed(()=>(props.interruptData.action_requests || []).some((action,index)=>action.name==='purchase_request_create'&&!partLookup.value[index]?.part))
watch(()=>JSON.stringify([props.interruptData.interrupt_id,props.interruptData.action_requests]),loadPurchaseInfo,{immediate:true})
onBeforeUnmount(()=>{lookupVersion++;lookupController?.abort()})
const executionPermissions={order_create:'procurement',order_update:'procurement',purchase_request_create:'procurement',inventory_adjust:'warehouse',master_change_request:'master'}
const reviewBlock=computed(()=>{
 const actions=props.interruptData.action_requests || []
 return !actions.length || actions.some(a=>!hasPermission(auth.user,executionPermissions[a.name])) ? '您的账号没有执行其中部分操作的权限，请联系相应业务人员处理。' : ''
})
const emit = defineEmits(['resume','close'])
function handleClose(){if(!props.submitting)emit('close')}

const supplementText = ref('')
const supplementInputRef = ref(null)
const choices = ref([])
const formValues = ref({})
const freeTextMode = ref(false)
const attempted = ref(false)
const submitError = ref('')
const formGroups = computed(() => orderFormGroups(props.interruptData))
const formErrors = computed(() => attempted.value && !freeTextMode.value ? {...orderFormErrors(props.interruptData, formValues.value),...selectionErrors()} : {})
const inputType = field => ['integer', 'decimal'].includes(field.type) ? 'number' : field.type
const fieldHelpId = field => `order-field-${props.interruptData.interrupt_id}-${field.path}`
watch(() => `${props.interruptData.interrupt_type}:${props.interruptData.interrupt_id}`, () => {
  choices.value = (props.interruptData.action_requests || []).map(() => '')
  supplementText.value = ''
  formValues.value = initialOrderValues(props.interruptData)
  freeTextMode.value = !props.interruptData.fields?.length
  attempted.value = false
  submitError.value = ''
}, { immediate: true })
const allChosen = computed(() => choices.value.length > 0 && choices.value.every(Boolean))
const catalog=ref({suppliers:[],parts:[]}),catalogLoading=ref(false),catalogReady=ref(false),catalogError=ref('')
let catalogController,catalogVersion=0
const needsCatalog=computed(()=>props.interruptData.interrupt_type==='order_info_supplement' && (props.interruptData.fields||[]).some(choiceType))
function choiceType(field){const name=field.name || field.path?.split('.').at(-1);return ['supplierId','supplier_id'].includes(name)?'supplier':['partId','part_id'].includes(name)?'part':''}
function fieldLabel(field){return ({supplier:'供应商',part:'物料'})[choiceType(field)] || field.label}
function fieldAriaLabel(field,group,section){return `订单${group.index+1} ${section.index===null?'':`明细${section.index+1} `}${fieldLabel(field)}`}
function supplierField(field){return (props.interruptData.fields||[]).find(f=>f.order_index===field.order_index && choiceType(f)==='supplier')}
function materialOptions(field){const supplier=supplierField(field);return supplier?catalog.value.parts.filter(part=>String(part.supplier_id)===String(formValues.value[supplier.path])):catalog.value.parts}
function choiceHint(field){if(!catalogReady.value || catalogError.value)return '';if(choiceType(field)==='supplier'&&!catalog.value.suppliers.length)return '暂无合作中的供应商，请您核实主数据。';if(choiceType(field)==='part'){const supplier=supplierField(field);if(supplier&&!formValues.value[supplier.path])return '请先选择供应商。';if(!materialOptions(field).length)return '该供应商暂无可采购物料，请您核实主数据。'}return ''}
function linePrice(field){return (props.interruptData.fields||[]).find(f=>f.order_index===field.order_index && f.detail_index===field.detail_index && ['unitPrice','unit_price'].includes(f.name || f.path?.split('.').at(-1)))}
function chooseField(field,value){
 formValues.value[field.path]=String(value)
 if(choiceType(field)==='supplier'){
  for(const partField of (props.interruptData.fields||[]).filter(f=>f.order_index===field.order_index && choiceType(f)==='part')){
   if(!materialOptions(partField).some(part=>String(part.id)===formValues.value[partField.path])){formValues.value[partField.path]='';const price=linePrice(partField);if(price)formValues.value[price.path]=''}
  }
 }else if(choiceType(field)==='part'){
  const part=materialOptions(field).find(part=>String(part.id)===String(value)),price=linePrice(field)
  if(price)formValues.value[price.path]=part?.purchase_price==null?'':String(part.purchase_price)
 }
}
function selectionErrors(){
 const errors={}
 for(const field of props.interruptData.fields||[]){const type=choiceType(field);if(!type)continue;const rows=type==='supplier'?catalog.value.suppliers:materialOptions(field);if(!rows.some(row=>String(row.id)===String(formValues.value[field.path])))errors[field.path]=type==='supplier'?'请选择合作中的供应商。':'请选择该供应商提供的可采购物料。'}
 return errors
}
async function loadCatalog(){
 catalogController?.abort();catalogVersion++;catalogReady.value=false;catalogError.value='';catalogLoading.value=false
 if(!canProcure.value || !needsCatalog.value)return
 const controller=new AbortController(),ticket=catalogVersion;catalogController=controller;catalogLoading.value=true
 try{const data=await procurementOptions(controller.signal);if(ticket===catalogVersion&&!controller.signal.aborted){catalog.value=data;catalogReady.value=true}}
 catch(error){if(ticket===catalogVersion&&!controller.signal.aborted)catalogError.value=error.message}
 finally{if(ticket===catalogVersion)catalogLoading.value=false}
}
onMounted(loadCatalog)
watch(()=>`${props.interruptData.interrupt_type}:${props.interruptData.interrupt_id}`,loadCatalog)
watch(canProcure,()=>{loadCatalog();loadPurchaseInfo()})
onBeforeUnmount(()=>{catalogVersion++;catalogController?.abort()})

// ============================================================
// 数据补充 — 提交
// ============================================================

function handleSupplement() {
  if (props.submitting || !canProcure.value) return
  attempted.value = true
  submitError.value = ''
  try {
    if(!freeTextMode.value && needsCatalog.value){
      if(catalogLoading.value || !catalogReady.value || catalogError.value)throw Error('请等待采购资料加载完成，或重新加载后再提交。')
      if(Object.keys(selectionErrors()).length)throw Error('请您核对供应商与物料选择。')
    }
    const response = orderFormResponse(props.interruptData, formValues.value, supplementText.value, freeTextMode.value)
    if (props.interruptData.workflow_type) response.workflow_type = props.interruptData.workflow_type
    emit('resume', response)
  } catch (error) {
    submitError.value = error.message
  }
}

// ============================================================
// HITL 审批 — 提交决策
// ============================================================

function handleDecision(decisionType) {
  if (props.submitting || reviewBlock.value || (decisionType==='approve' && purchaseInfoPending.value)) return
  emit('resume', approvalResponse(props.interruptData, props.interruptData.action_requests.map(() => decisionType)))
}

// ============================================================
// 格式化辅助函数
// ============================================================

function handleMixedDecision() {
  if (!props.submitting && !reviewBlock.value && allChosen.value && !purchaseInfoPending.value) emit('resume', approvalResponse(props.interruptData, choices.value))
}

function formatActionName(name) {
  const map = {
    'order_create': '新建采购订单',
    'order_update': '修改采购订单',
    'inventory_adjust': '调整库存',
    'purchase_request_create':'提交采购申请',
    'master_change_request':'变更基础资料'
  }
  return map[name] || '业务操作'
}

// CSS class
const bannerClass = computed(() => ({
  'banner-supplement': props.interruptData.interrupt_type === 'order_info_supplement',
  'banner-recovery': props.interruptData.interrupt_type === 'workflow_recovery',
  'banner-approval': props.interruptData.interrupt_type === 'hitl_approval'
}))
</script>

<style scoped>
.banner-recovery { border: 1px solid #cce0d8; background: #f4f8f3; }
.recovery-cancel-btn { background: #fff; border: 1px solid #c7ddd5; color: #237d6d; }
.order-form-area { padding: 0 20px 16px; }
.free-mode { display: flex; align-items: center; gap: 8px; margin: 0 0 16px; }
.order-group { min-width: 0; margin: 0 0 16px; padding: 16px; border: 1px solid #e6c78b; border-radius: 10px; }
.order-group legend, .note-label { font-weight: 600; color: #604615; }
.order-group h4 { margin: 12px 0 8px; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 190px), 1fr)); gap: 12px; }
.form-field { display: flex; flex-direction: column; gap: 5px; font-size: 14px; }
.form-field input,.form-field select { min-width: 0; width:100%; box-sizing:border-box; padding: 9px; border: 1px solid #c6d1c9; border-radius: 6px; background: white; color:inherit; font:inherit; }
.form-field input[aria-invalid="true"],.form-field select[aria-invalid="true"] { border-color: #b91c1c; }
.close-supplement{margin-left:auto;padding:8px 16px;border:1px solid #d4c59d;border-radius:7px;background:#fff;color:#604615;cursor:pointer;font:inherit}.close-supplement:disabled{opacity:.5;cursor:not-allowed}.catalog-notice{color:#85652c;font-size:13px}
.form-field small { color: #85652c; }
.form-error, .required, .form-field small.form-error { color: #b91c1c; }
.form-note { box-sizing: border-box; width: 100%; display: block; margin: 8px 0 12px; }

.field-label { min-width: 130px; white-space: normal; overflow-wrap: anywhere; padding-right: 18px; }
.field-value { white-space: pre-wrap; overflow-wrap: anywhere; }
.decision-select { margin-left: auto; padding: 6px 10px; border: 1px solid #c6d1c9; border-radius: 6px; background: #fff; color: #24373c; }
.interrupt-banner {
  margin: 16px 24px;
  border-radius: 16px;
  overflow: visible;
  flex-shrink: 0;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
  animation: slideUp 0.3s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.banner-supplement {
  border: 2px solid #f59e0b;
  background: #fffbeb;
}

.banner-approval {
  border: 2px solid #ef4444;
  background: #fef2f2;
}

/* 头部 */
.interrupt-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.interrupt-icon { font-size: 22px; }

.interrupt-title {
  font-size: 16px;
  font-weight: 700;
  color: #24373c;
}

/* 内容区 */
.interrupt-body {
  padding: 16px 20px;
}

.info-block {
  margin-bottom: 12px;
}

.info-block:last-child { margin-bottom: 0; }

.info-label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #697e79;
  margin-bottom: 4px;
}

.missing-fields .info-value {
  font-size: 15px;
  color: #b45309;
  font-weight: 600;
  background: #fef3c7;
  padding: 8px 14px;
  border-radius: 8px;
}

.info-pre {
  background: #f7f8f4;
  border: 1px solid #e7ebe7;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  margin: 0;
  line-height: 1.6;
}

/* 输入区 */
.interrupt-input-area {
  padding: 0 20px 16px;
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.supplement-textarea {
  flex: 1;
  border: 1px solid #e7ebe7;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
  resize: none;
  outline: none;
  font-family: inherit;
  min-height: 60px;
  transition: border-color 0.2s;
}

.supplement-textarea:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}

.interrupt-hint {
  padding: 0 20px 12px;
  font-size: 12px;
  color: #82908f;
}

/* 操作卡片 */
.action-card {
  background: #ffffff;
  border: 1px solid #fecaca;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
}

.action-card:last-child { margin-bottom: 0; }

.action-header { margin-bottom: 12px; }

.action-type {
  font-size: 15px;
  font-weight: 700;
  color: #b91c1c;
  background: #fef2f2;
  padding: 4px 14px;
  border-radius: 6px;
}

/* 订单详情表 */
.order-table {
  width: 100%;
  border-collapse: collapse;
}

.order-table td {
  padding: 6px 0;
  font-size: 14px;
}

.field-label {
  width: 100px;
  color: #697e79;
  font-weight: 600;
}

.field-value {
  color: #24373c;
}

.detail-table-wrap {
  margin-top: 12px;
}

.detail-label {
  font-size: 13px;
  font-weight: 600;
  color: #697e79;
  margin-bottom: 6px;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.detail-table th {
  background: #fef2f2;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  color: #b91c1c;
  border-bottom: 1px solid #fecaca;
}

.detail-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #eef2eb;
}

/* 按钮 */
.submit-btn {
  padding: 12px 24px;
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.supplement-btn {
  flex-shrink: 0;
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.3);
}

.approve-btn {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
}

.reject-btn {
  background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3);
}

.interrupt-actions {
  display: flex;
  gap: 12px;
  padding: 0 20px 16px;
  justify-content: center;
}
</style>
