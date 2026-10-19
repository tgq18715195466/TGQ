<template>
  <section class="business-result" :aria-label="title">
    <div class="business-heading">
      <h3>{{ title }}</h3>
      <span class="execution-outcome">{{ outcomeLabel }}</span>
    </div>
    <p v-if="executionMessage" class="execution-message">{{ executionMessage }}</p>
    <p class="result-count">{{ countLabel }}</p>
    <div v-if="columns.length && records.length" class="business-table-scroll" tabindex="0" :aria-label="`${title}明细，可滚动查看`">
      <table>
        <thead><tr><th v-for="column in columns" :key="column.key" scope="col">{{ column.label }}</th></tr></thead>
        <tbody>
          <tr v-for="(record, index) in records" :key="index">
            <td v-for="column in columns" :key="column.key">{{ cellValue(record, column) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!records.length && outcome === 'success'" class="result-empty">本次未返回记录。</p>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { businessCopy } from '../utils/answer-stream.js'
import { supplierStatus, orderStatus, stockStatus } from '../utils/dashboard.js'

const props = defineProps({result: {type:Object, required:true}})
// Explicit business field allowlist. IDs, transport fields and arbitrary record keys
// cannot become columns, even if a server-provided schema requests them.
// Keep public keys aligned with src/agent/business_results.py _SCHEMAS. The
// backend's *_name/state_label statuses are already Chinese text, not raw codes.
const fieldGroups = {
  text: {
    name:'名称', part_name:'物料名称', product_name:'商品名称', supplier_name:'供应商名称',
    part_code:'物料编码', supplier_code:'供应商编码', order_number:'订单编号', request_number:'申请编号',
    category:'分类', specification:'规格', model:'型号', unit:'单位', warehouse_name:'仓库名称',
    location:'库位', contact_person:'联系人', contact_name:'联系人', phone:'联系电话', email:'电子邮箱',
    address:'地址', remark:'备注', description:'说明', reason:'原因',
    expected_delivery_date:'预计交货日期', created_at:'创建时间', updated_at:'更新时间',
    status_name:'业务状态', supplier_status_name:'供应商状态', state_label:'业务状态',
    expected_date:'预计交货日期', order_time:'下单时间', actual_delivery_date:'实际交货日期',
    credit_rating:'信用等级', warehouse_location:'库位', last_inbound_time:'最近入库时间',
  },
  number: {
    quantity:'数量', current_quantity:'当前库存', available_quantity:'可用库存', stock_warning_value:'库存预警值',
    shortage:'缺口', suggested:'建议数量', safety_stock:'安全库存', pending_quantity:'待采购数量',
    purchased_quantity:'采购数量', received_quantity:'已收货数量', count:'数量',
    before_quantity:'调整前库存', delta:'调整数量', after_quantity:'调整后库存',
    on_order:'在途采购数量', demand:'已确认额外需求',
  },
  money: {unit_price:'单价', purchase_price:'采购价', suggested_retail_price:'建议零售价', total_amount:'总金额', total_price:'总价', subtotal:'小计', amount:'金额'},
  state: {status:'业务状态', order_status:'订单状态', supplier_status:'供应商状态', stock_state:'库存状态', state:'业务状态', enabled:'启用状态'},
}
const fields = Object.fromEntries(Object.entries(fieldGroups).flatMap(([format, entries]) =>
  Object.entries(entries).map(([key, label]) => [key, {label, format}])
))
const allowedFormats = new Set(['text', 'number', 'money', 'state', 'date'])
const columns = computed(() => {
  const seen = new Set()
  return (Array.isArray(props.result?.columns) ? props.result.columns : []).flatMap(column => {
    if (!column || !Object.hasOwn(fields, column.key) || seen.has(column.key)) return []
    const field = fields[column.key]
    const format = column.format ?? field.format
    if (!allowedFormats.has(format)) return []
    // Numeric/money/state keys retain their domain format to prevent raw status codes.
    if (field.format !== 'text' && format !== field.format) return []
    seen.add(column.key)
    const label = typeof column.label === 'string' && /^[\u4e00-\u9fff\d\s（）()、，：/元条件数量.\-]+$/.test(column.label) && /[\u4e00-\u9fff]/.test(column.label)
      ? column.label.trim() : field.label
    return [{key:column.key, label, format}]
  })
})
const records = computed(() => (Array.isArray(props.result?.records) ? props.result.records : [])
  .filter(record => record && typeof record === 'object' && !Array.isArray(record)))
const title = computed(() => businessCopy(props.result?.title, '业务结果'))
const outcomes = {success:'已完成', failure:'未完成', unknown:'执行状态待确认', needs_information:'需要补充信息', cancelled:'已取消'}
const outcome = computed(() => Object.hasOwn(outcomes, props.result?.execution?.outcome) ? props.result.execution.outcome : 'unknown')
const outcomeLabel = computed(() => outcomes[outcome.value])
const executionMessage = computed(() => businessCopy(props.result?.execution?.message))
const countLabel = computed(() => {
  const returned = records.value.length
  const total = Number.isSafeInteger(props.result?.total) && props.result.total >= returned ? props.result.total : null
  const complete = props.result?.complete === true && props.result.returned_count === returned && total === returned
  return `${complete ? '已返回' : '本次'}${returned}条/${total == null ? '总数待确认' : `共${total}条`}`
})

function decimal(value, money = false) {
  if ((typeof value !== 'string' && typeof value !== 'number') || (typeof value === 'number' && !Number.isFinite(value))) return '—'
  const text = String(value).trim()
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) return '—'
  const [integer, fraction = ''] = text.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  // Do not pass currency strings through Number: keep their exact decimal digits.
  return grouped + (money ? '.' + fraction.padEnd(2, '0') : fraction ? '.' + fraction : '')
}
function stateValue(value, key) {
  if (key === 'enabled') return value === true || value === 1 ? '已启用' : value === false || value === 0 ? '已停用' : '未知状态'
  let map = null
  const kind = typeof props.result?.kind === 'string' ? props.result.kind : ''
  if (key === 'stock_state' || /inventory|stock/.test(kind)) map = stockStatus
  if (key === 'supplier_status' || (key === 'status' && /supplier/.test(kind))) map = supplierStatus
  if (key === 'order_status' || (key === 'status' && /order/.test(kind))) map = orderStatus
  if ((typeof value === 'string' || typeof value === 'number') && map && Object.hasOwn(map, value)) return map[value]
  const states = {pending:'待审核', approved:'已审核', rejected:'已拒绝', cancelled:'已取消', completed:'已完成', active:'合作中', inactive:'暂停合作'}
  if (typeof value === 'string' && Object.hasOwn(states, value)) return states[value]
  return typeof value === 'string' && /^[\u4e00-\u9fff]+$/.test(value) ? value : '未知状态'
}
function cellValue(record, column) {
  const value = Object.hasOwn(record, column.key) ? record[column.key] : null
  if (value == null) return '—'
  if (column.format === 'number' || column.format === 'money') return decimal(value, column.format === 'money')
  if (column.format === 'state') return stateValue(value, column.key)
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  return '—'
}
</script>

<style scoped>
.business-result { margin: 0 0 14px; padding: 14px; border: 1px solid #cce0d8; border-radius: 10px; background: #fff; color: #243b39; min-width: 0; }
.business-result:last-child { margin-bottom: 0; }
.business-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
h3 { margin: 0; font-size: 15px; color: #1b7062; }
.execution-outcome { padding: 2px 9px; border-radius: 5px; background: #eaf4ed; color: #1b7062; font-size: 12px; }
.execution-message, .result-count, .result-empty { margin: 8px 0; font-size: 13px; color: #617c72; overflow-wrap: anywhere; }
.business-table-scroll { max-height: 420px; overflow: auto; overscroll-behavior: contain; border: 1px solid #e0e9e0; border-radius: 7px; }
.business-table-scroll:focus-visible { outline: 2px solid #238575; outline-offset: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
th, td { padding: 10px 12px; border-bottom: 1px solid #edf1eb; vertical-align: top; }
th { position: sticky; top: 0; background: #edf5ef; color: #356b60; white-space: nowrap; }
td { white-space: pre-wrap; overflow-wrap: anywhere; min-width: 70px; font-variant-numeric: tabular-nums; }
</style>
