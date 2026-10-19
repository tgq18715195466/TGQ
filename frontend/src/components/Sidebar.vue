<template>
  <aside class="sidebar">
    <!-- 侧边栏头部 -->
    <div class="sidebar-header">
      <div class="logo">
        <img class="logo-icon" src="/moto-logo.svg" alt="风驰 标志" />
        <div class="brand-copy"><strong>风驰</strong><span>采购智能工作台</span></div>
      </div>
    </div>
    <nav class="primary-navigation" aria-label="主导航">
      <p class="navigation-heading">工作空间</p>
      <button v-for="group in navigationGroups" :key="group.key" :aria-label="group.label" class="primary-link" :class="{selected:activeGroup===group.key}" :aria-current="activeGroup===group.key?'true':undefined" @click="$emit('navigate-group',group.key)">
        <span class="nav-mark" aria-hidden="true">{{ group.mark }}</span><span>{{ group.label }}</span><span class="nav-arrow" aria-hidden="true">›</span>
      </button>
    </nav>
    <!-- 侧边栏底部 -->
    <div class="sidebar-footer">
      <div class="footer-info">
        <span class="version">v1.0.0</span>
      </div>
    </div>
  </aside>
</template>

<script setup>
defineProps({navigationGroups:{type:Array,default:()=>[]},activeGroup:{type:String,default:'home'}})
defineEmits(['navigate-group'])
</script>
<style scoped>
.sidebar{width:232px;height:100%;min-height:0;display:flex;flex-direction:column;flex-shrink:0;overflow:auto;background:var(--color-nav);color:#dfe9e7}.sidebar-header{padding:28px 20px 20px;border-bottom:1px solid #31494c}.logo{display:flex;align-items:center;gap:12px}.logo-icon{width:48px;height:48px}.brand-copy{display:flex;flex-direction:column;gap:3px}.brand-copy strong{font-size:20px;letter-spacing:1px;color:#e5efd9}.brand-copy span{font-size:11px;letter-spacing:2px;color:#adc0c0}.primary-navigation{padding:18px 12px}.navigation-heading{font-size:11px;color:#91a195;margin:0 12px 12px;letter-spacing:2px}.primary-link{display:flex;align-items:center;gap:12px;width:100%;padding:12px;margin:4px 0;border:1px solid transparent;border-radius:10px;background:transparent;color:#c7d9d0;text-align:left;cursor:pointer;font:inherit;font-size:14px}.primary-link:hover{background:#294641}.primary-link.selected{background:#e7f2eb;border-color:#c8e0d2;color:#1e7867;font-weight:600}.primary-link:focus-visible{outline:2px solid #238575}.nav-mark{display:grid;place-items:center;width:28px;height:28px;border:1px solid #d9e5db;border-radius:8px;font-size:11px;background:#f8faf6;color:#406858;flex-shrink:0}.selected .nav-mark{background:#238575;color:white;border-color:#238575}.nav-arrow{margin-left:auto;color:#95ae9e}.sidebar-footer{margin-top:auto;padding:16px 20px;border-top:1px solid #31494c;font-size:12px;color:#a0b7b5}
@media(max-width:700px){.sidebar{width:154px}.sidebar-header{padding:16px 10px}.logo{gap:6px}.logo-icon{width:38px;height:38px}.brand-copy strong{font-size:17px}.brand-copy span{display:none}.primary-navigation{padding:12px 6px}.primary-link{gap:8px;padding:9px 6px;font-size:13px}.nav-arrow{display:none}}
</style>
