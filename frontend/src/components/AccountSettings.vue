<template>
 <section class="account-page">
  <h1>{{ view==='users'?'账号管理':'修改我的密码' }}</h1>
  <p>当前身份：{{ identity }} · {{ roleLabels[auth.user?.role] }}</p>
  <p v-if="error" class="auth-error" role="alert">{{ error }}</p><p v-if="success" role="status">{{ success }}</p>
  <form v-if="view==='password'" class="account-panel" @submit.prevent="changePassword">
   <label>当前密码<input v-model="oldPassword" type="password" autocomplete="current-password" required :disabled="busy"/></label>
   <label>新密码（12至256个字符）<input v-model="newPassword" type="password" autocomplete="new-password" minlength="12" maxlength="256" required :disabled="busy"/></label>
   <label>确认新密码<input v-model="confirmation" type="password" autocomplete="new-password" minlength="12" maxlength="256" required :disabled="busy"/></label>
   <button :disabled="busy">更新我的密码</button>
  </form>
  <template v-if="view==='users' && can('users')">
   <form class="account-panel" @submit.prevent="createUser">
    <h2>创建账号</h2><p>用户名可使用字母、数字、下划线、点和连字符；密码为12至256个字符。</p><label>用户名<input v-model.trim="draft.username" autocomplete="off" required maxlength="64" pattern="[A-Za-z0-9_.\-]+" :disabled="busy"/></label>
    <label>显示名称<input v-model.trim="draft.display_name" required maxlength="100" :disabled="busy"/></label>
    <label>初始密码<input v-model="draft.password" type="password" autocomplete="new-password" minlength="12" maxlength="256" required :disabled="busy"/></label>
    <label>角色<select v-model="draft.role" :disabled="busy"><option v-for="(label,key) in roleLabels" :value="key" :key="key">{{ label }}</option></select></label><button :disabled="busy">创建账号</button>
   </form>
   <div class="account-panel accounts"><header><h2>全部账号</h2><button @click="load" :disabled="busy">刷新列表</button></header>
    <p v-if="loading">正在读取账号…</p>
    <article v-for="user in users" :key="user.id" class="account-row">
     <h3>{{ user.display_name || user.username }} <small>{{ user.username }}{{ user.id===auth.user.id?' · 您':'' }}</small></h3><p>{{ user.enabled===false?'已停用':'已启用' }} · {{ roleLabels[user.role] }}</p>
     <form @submit.prevent="updateUser(user)">
      <label>账号角色<select v-model="edits[user.id].role" :disabled="busy"><option v-for="(label,key) in roleLabels" :key="key" :value="key">{{ label }}</option></select></label>
      <label>账号状态<select v-model="edits[user.id].enabled" :disabled="busy"><option :value="true">启用</option><option :value="false">停用</option></select></label>
      <label>重置密码（留空则不修改）<input v-model="edits[user.id].password" type="password" autocomplete="new-password" minlength="12" maxlength="256" :disabled="busy"/></label>
      <button :disabled="busy">保存账号设置</button>
     </form>
    </article><p v-if="!loading&&!users.length">暂无账号记录</p>
   </div>
  </template>
 </section>
</template>
<script setup>
import { ref, watch } from 'vue'
import { auth, identity, can, setUser, clearSession } from '../auth/state.js'
import { roleLabels } from '../auth/permissions.js'
import { authRequest } from '../api/http.js'
const props=defineProps({view:String})
const error=ref(''),success=ref(''),busy=ref(false),loading=ref(false),users=ref([]),edits=ref({})
const oldPassword=ref(''),newPassword=ref(''),confirmation=ref('')
const empty=()=>({username:'',display_name:'',password:'',role:'purchaser'})
const draft=ref(empty())
async function load(){loading.value=true;try{const data=await authRequest('users');users.value=data.users;edits.value=Object.fromEntries(data.users.map(u=>[u.id,{role:u.role,enabled:u.enabled!==false,password:''}]))}catch(e){error.value=e.message}finally{loading.value=false}}
async function perform(action){if(busy.value)return;busy.value=true;error.value='';success.value='';try{await action()}catch(e){error.value=e.message}finally{busy.value=false}}
function changePassword(){return perform(async()=>{if(newPassword.value!==confirmation.value)throw new Error('两次新密码不一致，请您重新确认。');await authRequest('password',{old_password:oldPassword.value,new_password:newPassword.value});oldPassword.value='';newPassword.value='';confirmation.value='';clearSession('您的密码已更新，请您使用新密码重新登录。')})}
function createUser(){return perform(async()=>{await authRequest('users',draft.value);draft.value=empty();await load();success.value='账号已创建。'})}
function updateUser(user){return perform(async()=>{
 const edit=edits.value[user.id], body={role:edit.role,enabled:edit.enabled,...(edit.password?{password:edit.password}:{})}
 await authRequest('users/'+encodeURIComponent(user.id),body,'PATCH');edit.password=''
 if(user.id===auth.user?.id){if(!body.enabled){clearSession('您的账号已停用。');return}if(body.password){clearSession('您的密码已重置，请您使用新密码重新登录。');return}const data=await authRequest('me');setUser(data.user);return}
 await load();success.value='账号设置已保存。'
})}
watch(()=>props.view,()=>{error.value='';success.value='';oldPassword.value='';newPassword.value='';confirmation.value='';draft.value=empty();if(props.view==='users'&&can('users'))load()},{immediate:true})
</script>
<style scoped>
.account-page{flex:1;overflow:auto;padding:28px 32px;background:#f5f7f3;color:#243b39}.account-panel{background:#fff;border:1px solid #dce8df;border-radius:12px;padding:24px;margin:20px 0;max-width:620px}.accounts{max-width:none}.accounts header{display:flex;align-items:center;justify-content:space-between}.account-row{border-top:1px solid #dce8df;padding:16px 0}.account-row small{font-weight:400;color:#697d76}.account-row form{display:flex;flex-wrap:wrap;gap:16px;align-items:end}.account-row label{flex:1;min-width:150px}.account-row button{margin-bottom:16px}h1{font-size:26px}h2{font-size:18px}@media(max-width:700px){.account-page{padding:16px}.account-panel{padding:16px}}
</style>
