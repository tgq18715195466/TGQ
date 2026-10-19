<template>
 <div class="table-scroll"><table><thead><tr><th v-for="col in columns" :key="col[0]" :class="{centered:centerColumns.includes(col[0])}">{{ col[1] }}</th><th v-if="$slots.actions">操作</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td v-for="col in columns" :key="col[0]" :class="{centered:centerColumns.includes(col[0])}">{{ display(row[col[0]],col[0]) }}</td><td v-if="$slots.actions"><div class="table-actions"><slot name="actions" :row="row"/></div></td></tr></tbody></table><p v-if="!rows.length" class="empty">暂无记录</p></div>
</template>
<script setup>
import {display} from '../utils/operations.js'
defineProps({rows:{type:Array,default:()=>[]},columns:Array,centerColumns:{type:Array,default:()=>[]}})
</script>
<style scoped>
.centered{text-align:center;font-variant-numeric:tabular-nums}.table-actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:10px}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;text-align:center}th,td{padding:12px;vertical-align:middle;border-bottom:1px solid #e5eee8;min-width:70px;max-width:350px;overflow-wrap:anywhere}th{background:#f0f5ef;white-space:nowrap}td{white-space:pre-wrap}.empty{text-align:center;color:#738a80;padding:20px}
</style>
