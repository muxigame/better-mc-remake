'use strict';
const $=(id)=>document.getElementById(id);
const message=(text,error=false)=>{const box=$('form-message');box.textContent=text;box.classList.toggle('error',error);box.hidden=false};
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||'请求失败');return body}
function showUser(user){$('guest-card').hidden=true;$('profile-card').hidden=false;$('profile-name').textContent=user.username;$('profile-email').textContent=user.email;$('profile-role').textContent=user.role==='admin'?'管理员':'玩家';$('profile-created').textContent=user.createdAt?new Date(user.createdAt).toLocaleDateString('zh-CN'):'—';$('admin-link').hidden=user.role!=='admin'}
$('logout-button').onclick=async()=>{await api('/api/v1/auth/logout',{method:'POST'});location.reload()};
(async()=>{const params=new URLSearchParams(location.search);if(params.get('auth'))message('统一账户登录没有完成，请重试。',true);try{const data=await api('/api/v1/auth/me');showUser(data.user)}catch{}})();
