'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const form = $('game-name-form');
  const input = $('game-name');
  const save = $('save-game-name');
  const dialog = $('rename-dialog');
  const state = { gameName: '', pendingName: '', busy: false };
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
  function updateButton() {
    save.disabled = state.busy || input.disabled || input.value.trim() === state.gameName || !input.validity.valid;
    save.textContent = state.busy ? '正在保存…' : '保存修改';
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
    state.gameName = player.gameName;
    input.value = state.gameName;
    input.disabled = false;
    $('player-identity').hidden = false;
    $('player-content').hidden = false;
    $('player-load-state').hidden = true;
    updateButton();
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
  input.addEventListener('input', () => { message(''); updateButton(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (save.disabled || !form.reportValidity()) return;
    state.pendingName = input.value.trim();
    $('rename-new-name').textContent = state.pendingName;
    dialog.showModal();
  });
  $('cancel-rename').addEventListener('click', () => dialog.close());
  $('confirm-rename').addEventListener('click', async () => {
    if (state.busy) return;
    dialog.close(); state.busy = true; input.disabled = true; updateButton();
    try {
      const result = await api('/api/v1/player/profile', { method: 'PATCH', body: JSON.stringify({ gameName: state.pendingName }) });
      render(result);
      message('已保存，下次启动游戏时将使用新名字。');
    } catch (error) { handleError(error); }
    finally { state.busy = false; input.disabled = false; updateButton(); }
  });
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
