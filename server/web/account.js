'use strict';
const $=(id)=>document.getElementById(id);
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});let body={};try{body=await response.json()}catch{}if(!response.ok){const error=new Error(body.detail||'请求失败');error.status=response.status;throw error}return body}
function money(cents){return `¥${(Number(cents||0)/100).toFixed(2)}`}
function showMessage(text,error=false){const box=$('profile-message');box.textContent=text;box.classList.toggle('error',error);box.hidden=false}
function render(data){
  const user=data.user||{},player=data.player||{};
  const nickname=user.nickname||user.username||'玩家';
  $('profile-nickname').textContent=nickname;
  $('profile-username').textContent=`@${user.username||'—'}`;
  $('profile-uid').textContent=user.uid||'—';
  $('account-nav-user').textContent=nickname;
  $('profile-game-name').value=player.gameName||'';
  $('player-points').textContent=Number(player.points||0).toLocaleString('zh-CN');
  $('player-balance').textContent=money(player.balanceCents);
  $('identity-nickname').textContent=nickname;
  $('identity-username').textContent=`@${user.username||'—'}`;
  $('identity-uid').textContent=user.uid||'—';
  $('profile-email').textContent=user.email||'未绑定';
  $('profile-email-status').textContent=user.email?(user.emailVerified?'已验证':'待验证'):'未绑定';
  $('profile-role').textContent=user.role==='admin'?'管理员':'玩家';
  $('profile-created').textContent=user.createdAt?new Date(user.createdAt).toLocaleDateString('zh-CN'):'—';
  $('admin-link').hidden=user.role!=='admin';
}
$('game-name-form').onsubmit=async(event)=>{event.preventDefault();const gameName=$('profile-game-name').value.trim();if(!confirm(`确定把 Better MC 游戏名改为 ${gameName}？\n\n当前服务器使用 Offline UUID，改名可能使服务器把你识别为新的玩家。`))return;try{const data=await api('/api/v1/player/profile',{method:'PATCH',body:JSON.stringify({gameName})});render(data);showMessage('游戏名已保存。下次启动客户端时会使用新的游戏名。')}catch(error){showMessage(error.message,true)}};
$('logout-button').onclick=async()=>{await api('/api/v1/auth/logout',{method:'POST'});location.href='/'};
(async()=>{try{render(await api('/api/v1/player/profile'))}catch(error){if(error.status===401){location.href='/api/v1/auth/login?return_to=%2Faccount.html'}else{showMessage(error.message,true)}}})();