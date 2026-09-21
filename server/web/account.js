'use strict';
const $=(id)=>document.getElementById(id);
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});let body={};try{body=await response.json()}catch{}if(!response.ok){const error=new Error(body.detail||'请求失败');error.status=response.status;throw error}return body}
function showUser(user){
  $('profile-nickname').textContent=user.nickname||user.username;
  $('profile-username').textContent=`@${user.username}`;
  $('profile-uid').textContent=user.uid;
  $('profile-game-name').textContent=user.gameName||'—';
  $('profile-email').textContent=user.email||'未绑定';
  $('profile-email-status').textContent=user.email?(user.emailVerified?'已验证':'待验证'):'未绑定';
  $('profile-role').textContent=user.role==='admin'?'管理员':'玩家';
  $('profile-created').textContent=user.createdAt?new Date(user.createdAt).toLocaleDateString('zh-CN'):'—';
  $('admin-link').hidden=user.role!=='admin';
}
$('logout-button').onclick=async()=>{await api('/api/v1/auth/logout',{method:'POST'});location.href='/'};
(async()=>{try{const data=await api('/api/v1/auth/me');showUser(data.user)}catch(error){if(error.status===401){location.href='/api/v1/auth/login?return_to=%2Faccount.html'}}})();
