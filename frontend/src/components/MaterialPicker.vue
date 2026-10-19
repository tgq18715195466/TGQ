<template>
 <div class="material-picker">
  <label>
   <span>{{ label }}</span>
   <select :value="modelValue" :aria-label="label" :required="required" :disabled="disabled || loading" @change="choose">
    <option value="">{{ loading ? '正在加载物料…' : '请选择物料' }}</option>
    <option v-for="part in items" :key="part.id" :value="part.id">{{ part.name }} · {{ part.part_code || '未维护编码' }}{{ part.model ? ' · '+part.model : '' }}</option>
   </select>
  </label>
  <small v-if="!loading && !items.length" role="status">暂无物料，请您先维护物料资料。</small>
 </div>
</template>
<script setup>
defineProps({modelValue:[Number,String],items:{type:Array,default:()=>[]},label:{type:String,default:'物料'},required:Boolean,loading:Boolean,disabled:Boolean})
const emit=defineEmits(['update:modelValue','change'])
function choose(event){const value=event.target.value;const id=value===''?'':Number(value);emit('update:modelValue',id);emit('change',id)}
</script>
<style scoped>
.material-picker{min-width:0;flex:1 1 260px;max-width:100%}.material-picker label{display:flex;flex-direction:column;gap:7px;font-size:13px}input,select{box-sizing:border-box;width:100%;min-width:0;padding:9px;border:1px solid #c7dbce;border-radius:6px;background:white;color:inherit;font:inherit}small{display:block;margin-top:7px;color:#617a6d;line-height:1.5}input:focus,select:focus{outline:2px solid #258574;outline-offset:1px}input:disabled,select:disabled{opacity:.6}
</style>
