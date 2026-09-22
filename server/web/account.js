'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const input = $('game-name');
  const entry = '/api/v1/auth/entry?return_to=%2Faccount.html';

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options, credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(typeof body.detail === 'string' ? body.detail : '暂时无法处理，请稍后重试。');
      error.status = response.status;
      throw error;
    }
    return body;
  }
  function message(text, error = false) {
    $('profile-message').textContent = text;
    $('profile-message').classList.toggle('is-error', error);
    $('profile-message').hidden = !text;
  }
  function render({ user, player }) {
    if (!user || !player || !player.gameName) throw new Error('玩家资料不完整，请重新登录。');
    const nickname = user.nickname || user.username || '玩家';
    $('player-display-name').textContent = nickname;
    $('player-avatar').textContent = Array.from(nickname)[0];
    $('player-handle').textContent = '@' + user.username;
    $('player-uid').textContent = 'UID ' + user.uid;
    $('identity-nickname').textContent = nickname;
    $('identity-username').textContent = '@' + user.username;
    $('identity-uid').textContent = user.uid;
    $('identity-email').textContent = user.email || '未绑定';
    $('identity-email-status').textContent = user.email ? (user.emailVerified ? '已验证' : '待验证') : '前往账户中心管理';
    $('identity-email-status').classList.toggle('is-unverified', !user.emailVerified);
    $('admin-link').hidden = user.role !== 'admin';
    input.value = player.loginName || player.gameName;
    $('game-display-name').value = player.displayName || nickname;
    $('legacy-identity-notice').hidden = !player.legacyGameName;
    $('legacy-game-name').textContent = player.legacyGameName || '';
    $('player-identity').hidden = false;
    $('player-content').hidden = false;
    $('player-load-state').hidden = true;
  }
  function handleError(error) {
    if (error.status === 401) {
      $('player-content').hidden = true;
      $('player-identity').hidden = true;
      location.replace(entry);
      return;
    }
    message(error.message, true);
  }
  async function load() {
    try { render(await api('/api/v1/player/profile')); }
    catch (error) {
      if (error.status === 401) return handleError(error);
      $('player-load-state').hidden = false;
      $('player-load-state').classList.add('is-error');
      $('player-load-state').textContent = '暂时无法读取玩家资料，请刷新页面重试。';
    }
  }
  $('logout-button').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await api('/api/v1/auth/logout', { method: 'POST' }); location.replace('/'); }
    catch (error) { handleError(error); button.disabled = false; }
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { $('player-content').hidden = true; $('player-identity').hidden = true; load(); }
  });
  load();
})();
