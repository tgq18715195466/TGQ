<template>
 <App v-if="auth.ready && auth.user" :key="auth.generation" />
 <main v-else class="auth-page">
  <section class="auth-card">
   <div class="auth-brand"><img src="/moto-logo.svg" alt="风驰标志"/><div><strong>风驰</strong><p>采购 · 库存 · 协同</p></div></div>
   <p v-if="!auth.ready" role="status">正在确认登录状态…</p>
   <template v-else>
    <h1>{{ auth.setupRequired ? '创建首个管理员' : '欢迎登录' }}</h1>
    <p class="auth-hint">{{ auth.setupRequired ? '首次使用，请您设置管理员账号。用户名可使用字母、数字、下划线、点和连字符；密码为12至256个字符。' : '请您使用自己的工作账号登录。' }}</p>
    <form v-if="!initialError" @submit.prevent="submit">
     <label>用户名<input v-model.trim="username" autocomplete="username" required maxlength="64" :pattern="auth.setupRequired?'[A-Za-z0-9_.\\-]+':undefined" :disabled="busy"/></label>
     <label v-if="auth.setupRequired">显示名称<input v-model.trim="displayName" autocomplete="name" required maxlength="100" :disabled="busy"/></label>
     <label>密码<input v-model="password" type="password" :autocomplete="auth.setupRequired?'new-password':'current-password'" :minlength="auth.setupRequired?12:1" maxlength="256" required :disabled="busy"/></label>
     <label v-if="auth.setupRequired">确认密码<input v-model="confirmation" type="password" autocomplete="new-password" required :disabled="busy"/></label>
     <button class="auth-primary" :disabled="busy">{{ busy?'正在处理…':auth.setupRequired?'创建管理员并进入':'登录工作台' }}</button>
    </form>
    <button v-if="initialError" @click="initialize" :disabled="busy">重新连接</button>
   </template>
   <p v-if="error || auth.notice" class="auth-error" role="alert">{{ error || auth.notice }}</p>
  </section>
 </main>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import App from './App.vue'
import { auth, setUser } from './auth/state.js'
import { authRequest } from './api/http.js'
const username=ref(''), displayName=ref(''), password=ref(''), confirmation=ref(''), error=ref(''), busy=ref(false), initialError=ref(false)
async function initialize(){
 auth.ready=false;error.value='';initialError.value=false;busy.value=true
 try {
  const status=await authRequest('status');auth.setupRequired=status.setup_required
  if(!status.setup_required){try{const data=await authRequest('me');setUser(data.user)}catch(e){if(e.status!==401)throw e}}
 }catch(e){error.value=e.message;initialError.value=true}
 finally{auth.ready=true;busy.value=false}
}
async function submit(){
 if(busy.value)return
 error.value='';auth.notice=''
 if(auth.setupRequired&&password.value!==confirmation.value){error.value='两次密码不一致，请您重新确认。';return}
 busy.value=true
 try {
  const data=await authRequest(auth.setupRequired?'setup':'login',{username:username.value,password:password.value,...(auth.setupRequired?{display_name:displayName.value}:{})})
  password.value='';confirmation.value='';setUser(data.user)
 }catch(e){error.value=e.message;if(auth.setupRequired&&e.status===409){try{const status=await authRequest('status');auth.setupRequired=status.setup_required}catch{}}}
 finally{busy.value=false}
}
onMounted(initialize)
</script>
<style>
.auth-page{min-height:100dvh;display:grid;place-items:center;padding:24px;background:#edf4ee;color:#243b39;box-sizing:border-box}.auth-card{width:min(100%,420px);background:#fff;border:1px solid #dbe7de;border-radius:20px;padding:36px;box-shadow:0 16px 50px #214b3610;box-sizing:border-box}.auth-brand{display:flex;align-items:center;gap:16px;margin-bottom:32px}.auth-brand img{width:56px}.auth-brand strong{font-size:26px;color:#237d6d}.auth-brand p,.auth-hint{font-size:13px;color:#697d76}.auth-card h1{font-size:24px}.auth-card label,.account-page label{display:flex;flex-direction:column;gap:8px;font-size:14px;margin:16px 0}.auth-card input,.account-page input,.account-page select{padding:11px;border:1px solid #c7dbce;border-radius:8px;font:inherit;box-sizing:border-box;min-width:0}.auth-card button,.account-page button,.identity-bar button{padding:10px 14px;border:1px solid #bed8cd;border-radius:8px;background:#eef7f1;color:#237d6d;cursor:pointer}.auth-card .auth-primary{width:100%;margin-top:12px;background:#238575;color:white}.auth-error{color:#a54125;background:#fff0e9;padding:12px;border-radius:8px;font-size:14px}.auth-card button:disabled,.account-page button:disabled{opacity:.5;cursor:wait}
</style>
