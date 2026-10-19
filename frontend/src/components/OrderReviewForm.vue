<template>
 <div class="review-overlay" @keydown.esc="close">
  <section class="review-dialog" role="dialog" aria-modal="true" aria-labelledby="order-review-title" tabindex="-1" ref="dialog" @keydown.tab="trapFocus">
   <header><h2 id="order-review-title">审核采购订单 {{ order?.order_number || '#'+orderId }}</h2><button type="button" @click="close" :disabled="saving">关闭审核</button></header>
   <p>当前审核身份：{{ identity }}</p>
   <p v-if="error" class="error" role="alert">{{ error }}</p>
   <button type="button" @click="load" :disabled="loading || saving">刷新审核明细</button>
   <p v-if="loading" role="status">正在核对最新订单内容…</p>
   <template v-else-if="order">
    <p>订单金额：{{ order.total_amount ?? '未填写' }}；预计交货：{{ order.expected_delivery_date || '未填写' }}</p>
    <div class="table-wrap"><table><thead><tr><th>物料</th><th>数量</th><th>单价</th></tr></thead><tbody><tr v-for="(line,index) in order.lines" :key="line.id || index"><td>{{ line.name || line.part_code || line.part_id }}</td><td>{{ line.quantity }}</td><td>{{ line.unit_price }}</td></tr></tbody></table></div>
    <p v-if="blocked" class="hint">{{ blocked }}</p>
    <form v-else @submit.prevent="submit">
     <fieldset :disabled="saving"><label>审核决定<select v-model="decision"><option value="approve">通过</option><option value="reject">驳回并取消</option></select></label>
      <label>审核意见<input v-model="reason" required maxlength="1000" placeholder="请您填写核对结论或驳回原因"/></label>
      <button class="primary" :disabled="saving || needsRefresh">{{ saving?'正在提交…':'确认审核' }}</button>
     </fieldset>
    </form>
   </template>
  </section>
 </div>
</template>
<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { auth, identity, can } from '../auth/state.js'
import { approvalBlock } from '../auth/permissions.js'
import { purchaseRequest } from '../api/purchase-orders.js'
const props=defineProps({orderId:{type:Number,required:true}}),emit=defineEmits(['close','saved'])
const order=ref(null),loading=ref(false),saving=ref(false),error=ref(''),decision=ref('approve'),reason=ref(''),needsRefresh=ref(false),dialog=ref(null)
let controller,alive=true
const blocked=computed(()=>approvalBlock(auth.user,order.value)||(Number(order.value?.status)!==1?'该订单已不处于待审核状态，请您刷新采购订单列表。':!order.value?.review_version?'缺少订单版本，请您刷新审核明细后重试。':''))
function close(){if(!saving.value)emit('close')}
function trapFocus(event){const nodes=dialog.value?.querySelectorAll?.('button:not(:disabled),input:not(:disabled),select:not(:disabled)');if(!nodes?.length)return;const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&(document.activeElement===first||document.activeElement===dialog.value)){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
async function load(){
 if(saving.value||!can('approve'))return
 controller?.abort();const active=new AbortController();controller=active;loading.value=true;error.value='';order.value=null
 try{const data=await purchaseRequest('operations/receiving/'+props.orderId,{signal:active.signal});if(alive&&!active.signal.aborted){order.value=data;needsRefresh.value=false}}
 catch(e){if(e.name!=='AbortError')error.value=e.message}
 finally{if(controller===active)loading.value=false}
}
async function submit(){
 if(saving.value||loading.value||needsRefresh.value||blocked.value)return
 error.value=''
 if(!reason.value.trim()){error.value='请您填写审核意见。';return}
 saving.value=true
 try{const result=await purchaseRequest('operations/receiving/'+props.orderId+'/review',{body:{decision:decision.value,reason:reason.value.trim(),expected_version:order.value.review_version}});if(alive)emit('saved',result)}
 catch(e){if(e.name!=='AbortError'){error.value=e.message;needsRefresh.value=true}}
 finally{saving.value=false}
}
onMounted(async()=>{await nextTick();dialog.value?.focus();await load()})
onBeforeUnmount(()=>{alive=false;controller?.abort()})
</script>
<style scoped>
.review-overlay{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:24px;background:#183b3566}.review-dialog{box-sizing:border-box;width:min(740px,100%);max-height:90dvh;overflow:auto;padding:26px;border:1px solid #c7dbce;border-radius:16px;background:#fff;color:#243b39;box-shadow:0 20px 70px #173c3533}.review-dialog:focus{outline:none}header{display:flex;justify-content:space-between;align-items:center;gap:16px}h2{font-size:22px;margin:0}p{font-size:13px;line-height:1.7}label{display:flex;flex-direction:column;gap:8px;margin:16px 0;font-size:14px}input,select{padding:10px;border:1px solid #c7dbce;border-radius:7px;font:inherit;background:white;color:inherit}fieldset{border:0;padding:0;margin:0;min-width:0}button{border:1px solid #bed8cd;background:#eef7f1;color:#237d6d;padding:10px 14px;border-radius:7px;cursor:pointer;font:inherit;font-size:13px}.primary{background:#238575;color:white}button:disabled{opacity:.5;cursor:not-allowed}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;text-align:center}th,td{padding:10px;border-bottom:1px solid #e1ebe3}.error{background:#fff0e9;color:#a54125;padding:12px;border-radius:8px;white-space:pre-wrap}.hint{color:#617a6d}@media(max-width:650px){.review-overlay{padding:10px}.review-dialog{padding:16px}header{align-items:start}}
</style>
