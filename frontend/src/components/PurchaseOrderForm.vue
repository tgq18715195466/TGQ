<template>
 <div class="order-overlay" @keydown.esc="close">
  <section class="order-dialog" role="dialog" aria-modal="true" aria-labelledby="purchase-order-title" ref="dialog" tabindex="-1" @keydown.tab="trapFocus">
   <header><div><p class="eyebrow">风驰 · 采购管理</p><h2 id="purchase-order-title">新建采购订单</h2></div><button type="button" @click="close" :disabled="saving">关闭采购表单</button></header>
   <p class="hint">提交后订单为待审核状态，请由其他审批人核实并审核。当前身份：{{ identity }}</p>
   <p v-if="error" class="error" role="alert">{{ error }}</p>
   <p v-if="loadNotice" class="hint" role="status">{{ loadNotice }}</p>
   <p v-if="!can('procurement')" class="error">您没有创建采购订单的权限。</p>
   <form v-else @submit.prevent="submit" novalidate>
    <fieldset :disabled="saving">
     <div class="fields">
      <label>合作中供应商<select v-model.number="supplierId" @change="selectSupplier()" required :disabled="supplierLoading"><option value="">请选择供应商</option><option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.name }} · {{ supplier.supplier_code || supplier.id }}</option></select></label>
      <label>预计交货日期<input v-model="deliveryDate" type="date" :min="today()" required/></label>
     </div>
     <p v-if="!supplierLoading && !loadNotice && !suppliers.length" class="hint">暂无合作中的供应商，请您核实供应商合作状态。</p>
     <p v-if="partsLoading" role="status">正在读取供应商物料…</p>
     <p v-else-if="supplierId && !loadNotice && !parts.length" class="hint">该供应商暂无可选物料，请您核实主数据。</p>
     <div v-for="(line,index) in lines" :key="line.key" class="order-line">
      <h3>明细 {{ index+1 }}</h3>
      <div class="fields">
       <label>物料<select v-model.number="line.partId" :aria-label="`第${index+1}行物料`" @change="selectPart(line)" :disabled="partsLoading || !supplierId" required><option value="">请选择物料</option><option v-for="part in parts" :key="part.id" :value="part.id">{{ part.name }} · {{ part.part_code || part.id }}</option></select></label>
       <label>数量（正整数）<input v-model="line.quantity" :aria-label="`第${index+1}行数量`" type="number" min="1" step="1" required/></label>
       <label>单价（不小于0）<input v-model="line.unitPrice" :aria-label="`第${index+1}行单价`" type="number" min="0" step="any" required/></label>
       <button type="button" @click="lines.splice(index,1)" :disabled="lines.length===1" :aria-label="`删除第${index+1}行`">删除明细</button>
      </div>
     </div>
     <footer><button type="button" @click="addLine" :disabled="partsLoading">添加明细</button><button class="primary" type="submit" :disabled="saving || supplierLoading || partsLoading">{{ saving?'正在提交…':'提交采购订单（待审核）' }}</button></footer>
    </fieldset>
   </form>
  </section>
 </div>
</template>
<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { can, identity } from '../auth/state.js'
import { purchaseRequest } from '../api/purchase-orders.js'
import { operationKey } from '../utils/operations.js'
const props=defineProps({initialPart:{type:Object,default:null}})
const emit=defineEmits(['close','saved'])
const dialog=ref(null),supplierId=ref(''),suppliers=ref([]),parts=ref([]),deliveryDate=ref(''),error=ref(''),loadNotice=ref(''),saving=ref(false),supplierLoading=ref(false),partsLoading=ref(false)
let key=0, supplierController,partsController,alive=true,orderNumber=null
const emptyLine=()=>({key:++key,partId:'',quantity:'1',unitPrice:''})
const lines=ref([emptyLine()])
function today(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function close(){if(!saving.value)emit('close')}
function trapFocus(event){
 const nodes=dialog.value?.querySelectorAll?.('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]')
 if(!nodes?.length)return
 const first=nodes[0],last=nodes[nodes.length-1]
 if(event.shiftKey && (document.activeElement===first || document.activeElement===dialog.value)){event.preventDefault();last.focus()}
 else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus()}
}
async function loadSuppliers(prefill=null){
 if(!can('procurement')||saving.value)return
 supplierController?.abort();const controller=new AbortController();supplierController=controller;supplierLoading.value=true;error.value='';loadNotice.value=''
 try{
  const allSuppliers=[]
  let current=1
  while(true){
   const params=new URLSearchParams({status:'1',current:String(current),size:'100'})
   const data=await purchaseRequest('dashboard/suppliers?'+params,{signal:controller.signal})
   if(!alive || controller.signal.aborted)return
   if(!Array.isArray(data.records))throw new Error('供应商数据读取异常，请您关闭表单后重试。')
   allSuppliers.push(...data.records)
   if(allSuppliers.length>=Number(data.total))break
   if(!data.records.length)throw new Error('供应商列表已变化，请您关闭表单后重试。')
   current++
  }
  suppliers.value=allSuppliers.filter(s=>Number(s.status)===1)
  if(prefill){
   if(!suppliers.value.some(s=>Number(s.id)===Number(prefill.supplier_id))){loadNotice.value='该物料的供应商不在合作中，不能创建采购订单。请您核实供应商状态。';return}
   supplierId.value=Number(prefill.supplier_id);await selectSupplier(prefill)
  }
 }catch(e){if(e.name!=='AbortError')loadNotice.value='供应商数据加载失败，请您重新打开表单再试。'}
 finally{if(supplierController===controller)supplierLoading.value=false}
}
async function selectSupplier(prefill=null){
 partsController?.abort();parts.value=[];lines.value=[emptyLine()];error.value='';loadNotice.value=''
 if(!supplierId.value)return
 const controller=new AbortController();partsController=controller;partsLoading.value=true
 try{
  const selectedSupplier=Number(supplierId.value), allParts=[]
  let current=1
  while(true){
   const params=new URLSearchParams({supplierId:String(selectedSupplier),current:String(current),size:'100'})
   const data=await purchaseRequest('parts/page?'+params,{signal:controller.signal})
   if(!alive || controller.signal.aborted)return
   if(!Array.isArray(data.records))throw new Error('物料数据读取异常，请您重新选择供应商后重试。')
   allParts.push(...data.records)
   if(allParts.length>=Number(data.total))break
   if(!data.records.length)throw new Error('物料列表已变化，请您重新选择供应商后重试。')
   current++
  }
  parts.value=allParts.filter(p=>p.enabled!==false && Number(p.supplier_id)===selectedSupplier)
  if(prefill){
   const part=parts.value.find(p=>Number(p.id)===Number(prefill.id))
   if(part){lines.value[0].partId=Number(part.id);selectPart(lines.value[0])}
   else loadNotice.value='该物料不在当前供应商的可采购明细中，请您重新选择物料。'
  }
 }catch(e){if(e.name!=='AbortError')loadNotice.value='供应商物料加载失败，请您重新打开表单再试。'}
 finally{if(partsController===controller)partsLoading.value=false}
}
function selectPart(line){const part=parts.value.find(p=>Number(p.id)===Number(line.partId));line.unitPrice=part?.purchase_price==null?'':String(part.purchase_price)}
function addLine(){if(!saving.value)lines.value.push(emptyLine())}
function payload(){
 if(!suppliers.value.some(s=>Number(s.id)===Number(supplierId.value)&&Number(s.status)===1))throw new Error('请您选择合作中的供应商。')
 const date=deliveryDate.value
 if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(Date.parse(date))||new Date(date).toISOString().slice(0,10)!==date)throw new Error('请您填写有效的预计交货日期。')
 if(date<today())throw new Error('预计交货日期不能早于今天，请您重新选择。')
 if(!lines.value.length)throw new Error('请您至少添加一行物料明细。')
 const orderDetail=lines.value.map((line,index)=>{
  const part=parts.value.find(p=>Number(p.id)===Number(line.partId))
  if(!part)throw new Error(`第${index+1}行：请您选择当前供应商的物料。`)
  const quantity=Number(line.quantity),price=String(line.unitPrice).trim()
  if(!Number.isSafeInteger(quantity)||quantity<=0)throw new Error(`第${index+1}行：数量必须为正整数。`)
  if(!/^\d+(\.\d+)?$/.test(price)||!Number.isFinite(Number(price))||Number(price)<0)throw new Error(`第${index+1}行：单价必须为不小于0的有效数字。`)
  return {partId:Number(part.id),quantity,unitPrice:price}
 })
 // Keep this identity for every retry of the same draft, including uncertain network outcomes.
 if(!orderNumber)orderNumber='PO'+Date.now().toString(36).toUpperCase()+operationKey().replace(/-/g,'').slice(0,20).toUpperCase()
 return {orderNumber,supplierId:Number(supplierId.value),expectedDeliveryDate:date,orderDetail}
}
async function submit(){
 if(saving.value||supplierLoading.value||partsLoading.value||!can('procurement'))return
 error.value=''
 try{
  const body=payload();saving.value=true
  const result=await purchaseRequest('orders/create',{body})
  if(alive){
   orderNumber=null;deliveryDate.value='';lines.value=[emptyLine()]
   emit('saved',result)
  }
 }
 catch(e){if(e.name!=='AbortError')error.value=e.message}
 finally{saving.value=false}
}
onMounted(async()=>{await nextTick();dialog.value?.focus();await loadSuppliers(props.initialPart)})
onBeforeUnmount(()=>{alive=false;supplierController?.abort();partsController?.abort()})
</script>
<style scoped>
.order-overlay{position:fixed;inset:0;z-index:50;background:#183b3566;display:grid;place-items:center;padding:24px}.order-dialog{box-sizing:border-box;width:min(920px,100%);max-height:90dvh;overflow:auto;border:1px solid #c7dbce;border-radius:16px;background:#fff;padding:26px;color:#243b39;box-shadow:0 20px 70px #173c3533}.order-dialog:focus{outline:none}header,footer{display:flex;justify-content:space-between;align-items:center;gap:16px}h2{font-size:23px;margin:0}h3{font-size:15px;margin:0 0 12px}.eyebrow{color:#258574;font-size:12px;margin:0 0 8px}.hint{font-size:13px;color:#617a6d;line-height:1.7}.fields{display:flex;align-items:end;gap:12px;flex-wrap:wrap}label{display:flex;flex-direction:column;gap:7px;flex:1 1 180px;font-size:13px}input,select{box-sizing:border-box;width:100%;min-width:0;padding:10px;border:1px solid #c7dbce;border-radius:7px;background:#fff;font:inherit;color:inherit}fieldset{border:0;padding:0;margin:0;min-width:0}.order-line{margin:18px 0;padding:18px;background:#f5f8f4;border:1px solid #dce8df;border-radius:10px}button{border:1px solid #bed8cd;background:#eef7f1;color:#237d6d;padding:10px 14px;border-radius:7px;cursor:pointer;font:inherit;font-size:13px}.primary{background:#238575;color:#fff}button:disabled{opacity:.5;cursor:not-allowed}.error{padding:12px;border-radius:8px;background:#fff0e9;color:#a54125;white-space:pre-wrap}@media(max-width:650px){.order-overlay{padding:10px}.order-dialog{padding:16px}.fields label{flex-basis:100%}header{align-items:start}footer{flex-wrap:wrap}}
</style>
