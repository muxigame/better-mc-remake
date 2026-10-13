'use strict';
(() => {
  const BASE = '/api/v1/player/ysm/models';
  const DEFAULT_LIMITS = {maxFileBytes: 67108864, maxUserBytes: 268435456, maxModels: 100};
  const size = bytes => Number(bytes) < 1048576 ? (Number(bytes) / 1024).toFixed(1) + ' KB' : (Number(bytes) / 1048576).toFixed(1) + ' MB';
  const validationLabels = {pending: '等待验证', verified: '验证通过', rejected: '验证未通过'};
  const syncLabels = {blocked: '尚未同步', pending: '等待服务器同步', staged: '已保存文件，尚未加载', ready: '服务器已就绪'};
  const activeStates = ['hashing', 'checking', 'referencing', 'uploading'];
  function fileError(file, limits = DEFAULT_LIMITS) {
    if (!file || !/\.ysm$/i.test(file.name || '') || /[\\/\x00-\x1f\x7f]/.test(file.name)) return '请选择名称有效的 .ysm 文件。';
    if (!file.size) return '文件为空。';
    if (file.size > limits.maxFileBytes) return '文件超过单文件限制（' + size(limits.maxFileBytes) + '）。';
    return '';
  }
  function canUse(model) { return !model.deletedAt && model.validationStatus === 'verified' && model.syncStatus === 'ready' && model.canSelect === true; }
  function canDownload(model) { return !model.deletedAt && model.validationStatus === 'verified' && model.canDownload === true; }
  function detail(body, fallback) { return body && typeof body.detail === 'string' ? body.detail : fallback; }
  function isAccountChanged(body) { return !!body && (body.detail === 'account_changed' || body.code === 'account_changed' || (body.detail && body.detail.code === 'account_changed')); }
  function profileUID(profile) {
    const loginName = profile && profile.player && profile.player.loginName;
    if (typeof loginName === 'string' && /^[1-9][0-9]{4,15}$/.test(loginName)) return loginName;
    const uid = profile && profile.user && profile.user.uid;
    if (typeof uid === 'string' && /^[1-9][0-9]{4,15}$/.test(uid)) return uid;
    if (typeof uid === 'number' && Number.isSafeInteger(uid) && /^[1-9][0-9]{4,15}$/.test(String(uid))) return String(uid);
    throw new Error('账户资料缺少精确的稳定 UID，请重新登录。');
  }
  function responseBody(xhr) { try { return JSON.parse(xhr.responseText || '{}'); } catch (_) { return {}; } }
  function newKey() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0,8), hex.slice(8,12), hex.slice(12,16), hex.slice(16,20), hex.slice(20)].join('-');
  }
  async function sha256File(file) {
    if (!Number.isFinite(file.size) || file.size < 1 || file.size > DEFAULT_LIMITS.maxFileBytes) throw new Error('文件超过校验限制。');
    const buffer = await file.arrayBuffer();
    if (buffer.byteLength !== file.size || buffer.byteLength > DEFAULT_LIMITS.maxFileBytes) throw new Error('文件读取大小不一致。');
    const digest = await globalThis.crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  // Retry keeps both the original key and request parameters, including after an unknown outcome.
  function createUploadTask(file, visibility, options = {}) {
    const task = {file, visibility: visibility === 'public' ? 'public' : 'private', key: options.key || newKey(),
      state: 'queued', progress: 0, error: '', model: null, sent: false, route: null, reusedPublic: false};
    let active = null, digest = null, publicId = null;
    const changed = () => { if (options.onChange) options.onChange(task); };
    task.start = () => {
      if (active || task.state === 'complete') return Promise.resolve(task);
      task.progress = 0; task.error = ''; task.sent = true;
      const operation = {xhr: null, controller: typeof AbortController === 'function' ? new AbortController() : null}; active = operation;
      return new Promise(resolve => {
        let finished = false;
        function finish(state, message, body) {
          if (finished) return; finished = true; if (active === operation) active = null;
          task.state = state; task.error = message || '';
          if (body && body.model) task.model = body.model;
          if (state === 'complete') task.progress = 100;
          changed(); resolve(task);
        }
        operation.cancel = () => {
          finish('cancelled', '已取消；如果请求已送达，重试会核对原请求编号。');
          if (operation.controller) operation.controller.abort();
          if (operation.xhr) operation.xhr.abort();
        };
        function current() {
          if (finished || active !== operation) return false;
          if (options.isCurrent && !options.isCurrent()) { operation.cancel(); return false; }
          return true;
        }
        async function request(url, requestOptions = {}) {
          const response = await (options.fetch || globalThis.fetch)(url, {...requestOptions,
            credentials: 'same-origin', cache: 'no-store', ...(operation.controller ? {signal: operation.controller.signal} : {}),
            headers: {Accept: 'application/json', ...(options.expectedUID ? {'X-Muxi-Expected-UID': options.expectedUID} : {}), ...(requestOptions.headers || {})}});
          let body = {}; try { body = await response.json(); } catch (_) {}
          if (!current()) return null;
          if (response.status === 409 && isAccountChanged(body)) {
            finish('error', '账户已切换，请重新登录后选择文件。');
            if (options.onAccountChanged) options.onAccountChanged();
            return null;
          }
          if (response.status === 401 && options.onUnauthorized) options.onUnauthorized();
          return {response, body};
        }
        function sendFile() {
          if (!current()) return;
          task.state = 'uploading';
          const xhr = options.createXHR ? options.createXHR() : new XMLHttpRequest(); operation.xhr = xhr;
          xhr.upload.onprogress = event => {
            if (!finished && event.lengthComputable) { task.progress = Math.min(99, Math.round(event.loaded / event.total * 100)); changed(); }
          };
          xhr.onload = () => {
            if (!current()) return;
            const body = responseBody(xhr);
            if (xhr.status === 409 && isAccountChanged(body)) {
              finish('error', '账户已切换，请重新登录后选择文件。');
              if (options.onAccountChanged) options.onAccountChanged();
              return;
            }
            if (xhr.status >= 200 && xhr.status < 300 && body.model) finish('complete', '', body);
            else {
              finish('error', detail(body, '上传未确认成功，可以重试。'));
              if (xhr.status === 401 && options.onUnauthorized) options.onUnauthorized();
            }
          };
          xhr.onerror = () => finish('error', '连接中断，结果尚未确认。重试会沿用原请求编号。');
          xhr.ontimeout = () => finish('error', '上传超时，结果尚未确认。重试会沿用原请求编号。');
          xhr.onabort = () => finish('cancelled', '上传已取消，服务器可能已收到文件；重试会核对原请求。');
          try {
            xhr.open('POST', BASE + '?filename=' + encodeURIComponent(file.name) + '&visibility=' + task.visibility);
            xhr.withCredentials = true; xhr.timeout = 300000;
            xhr.setRequestHeader('Accept', 'application/json');
            xhr.setRequestHeader('Content-Type', 'application/octet-stream');
            xhr.setRequestHeader('Idempotency-Key', task.key);
            if (options.expectedUID) xhr.setRequestHeader('X-Muxi-Expected-UID', options.expectedUID);
            changed(); xhr.send(file);
          } catch (_) { finish('error', '无法发送文件，请稍后重试。'); }
        }
        async function prepare() {
          try {
            if (!current()) return;
            const canHash = options.hashFile || (globalThis.crypto && globalThis.crypto.subtle && typeof file.arrayBuffer === 'function');
            if (!task.route && canHash) {
              if (!digest) {
                task.state = 'hashing'; changed();
                const computed = await (options.hashFile || sha256File)(file);
                if (!current()) return;
                digest = computed;
                if (!/^[a-f0-9]{64}$/.test(digest)) throw new Error('文件校验结果无效。');
              }
              task.state = 'checking'; changed();
              const lookup = await request('/api/v1/ysm/models/public/by-sha256/' + digest);
              if (!lookup || !current()) return;
              if (lookup.response.status === 404) task.route = 'upload';
              else if (lookup.response.ok && lookup.body.model && /^p_[A-Za-z0-9_-]+$/.test(lookup.body.model.id)) {
                publicId = lookup.body.model.id; task.route = 'reference';
              } else throw new Error(detail(lookup.body, '公共模型查重失败，请重试；尚未上传文件。'));
            }
            if (!current()) return;
            if (!task.route) task.route = 'upload';
            if (task.route === 'reference') {
              task.state = 'referencing'; changed();
              const result = await request(BASE + '/references', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': task.key},
                body: JSON.stringify({publicId, filename: file.name, visibility: task.visibility})});
              if (!result || !current()) return;
              if (!result.response.ok || !result.body.model) throw new Error(detail(result.body, '公开来源已改变或引用未确认成功，请重试。'));
              task.reusedPublic = true; finish('complete', '', result.body);
            } else sendFile();
          } catch (error) {
            if (current()) finish('error', error && error.message ? error.message : '校验或查重失败，请重试；尚未上传文件。');
          }
        }
        prepare();
      });
    };
    task.cancel = () => {
      if (active) active.cancel();
      else if (task.state !== 'complete') { task.state = 'cancelled'; task.error = '已取消。'; changed(); }
    };
    task.setVisibility = value => {
      if (!task.sent && task.state === 'queued') { task.visibility = value === 'public' ? 'public' : 'private'; changed(); }
    };
    return task;
  }
  // Expose deterministic helpers to the local interaction tests; no credentials or user data live here.
  const helpers = {fileError, canUse, canDownload, sha256File, isAccountChanged, profileUID, createUploadTask, DEFAULT_LIMITS};
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (typeof document === 'undefined' || !document.getElementById('ysm-app')) return;

  const $ = id => document.getElementById(id);
  const uploads = [], rows = new Map();
  let authenticated = false, queueRunning = false, authEpoch = 0, listEpoch = 0, publicEpoch = 0;
  let limits = {...DEFAULT_LIMITS}, models = [], selectedModelId = null, expectedUID = null;
  function node(tag, className, text) {
    const element = document.createElement(tag); if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function notify(text, error = false) { $('ysm-message').textContent = text; $('ysm-message').classList.toggle('is-error', error); }
  function loginRequired() {
    authenticated = false; authEpoch++; listEpoch++;
    uploads.forEach(task => { if (activeStates.includes(task.state)) task.cancel(); });
    models = []; selectedModelId = null; $('ysm-my-list').replaceChildren();
    $('ysm-private-content').hidden = true; $('ysm-login').hidden = false;
    notify('账户登录已失效，请重新登录。', true); renderPublic();
  }
  function accountChanged() {
    loginRequired(); expectedUID = null;
    uploads.length = 0; rows.clear(); $('ysm-upload-list').replaceChildren();
    if ($('ysm-confirm').open) $('ysm-confirm-cancel').click();
    notify('账户已切换，原账户文件队列已清空。请重新登录或刷新页面后选择文件。', true);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, credentials: 'same-origin', cache: 'no-store',
      headers: {Accept: 'application/json', ...(expectedUID ? {'X-Muxi-Expected-UID': expectedUID} : {}), ...(options.body ? {'Content-Type': 'application/json'} : {}), ...(options.headers || {})}});
    let body = {}; try { body = await response.json(); } catch (_) {}
    if (!response.ok) {
      if (response.status === 409 && isAccountChanged(body)) accountChanged();
      if (response.status === 401) loginRequired();
      const error = new Error(isAccountChanged(body) ? '账户已切换，请重新登录或刷新页面。' : detail(body, '暂时无法处理请求，请稍后重试。')); error.status = response.status; throw error;
    }
    return body;
  }
  function button(text, action, disabled = false) {
    const result = node('button', 'button button-outline', text); result.type = 'button'; result.disabled = disabled;
    result.addEventListener('click', async () => {
      if (result.disabled) return;
      result.disabled = true;
      try { await action(); } finally { result.disabled = disabled; }
    }); return result;
  }
  function visibilitySelect(value) {
    const select = node('select');
    [['private', '私有 · 仅自己'], ['public', '公开 · 所有人']].forEach(([key, label]) => {
      const option = node('option', '', label); option.value = key; select.append(option);
    }); select.value = value; return select;
  }
  function updateUpload(task) {
    const row = rows.get(task); if (!row) return;
    const stateLabels = {queued: '等待上传', hashing: '正在计算文件校验值（尚未上传）', checking: '正在检查公共模型（尚未上传）', referencing: '正在保存本人引用（无需上传文件）', uploading: '上传中 ' + task.progress + '%', complete: (task.reusedPublic ? '引用已保存' : '上传完成') + ' · ' + (validationLabels[task.model && task.model.validationStatus] || '等待验证'), error: '处理失败', cancelled: '已取消'};
    row.status.textContent = stateLabels[task.state] + (task.error ? ' · ' + task.error : '');
    row.status.classList.toggle('is-error', task.state === 'error'); row.progress.value = task.progress;
    row.progress.hidden = task.state !== 'uploading' && !(task.state === 'complete' && !task.reusedPublic);
    row.visibility.disabled = task.sent || task.state !== 'queued';
    row.cancel.hidden = task.state !== 'queued' && !activeStates.includes(task.state);
    row.retry.hidden = !['error', 'cancelled'].includes(task.state);
    row.retry.disabled = queueRunning || !authenticated;
    $('ysm-upload-start').disabled = queueRunning || !authenticated || !uploads.some(x => x.state === 'queued');
  }
  function addFiles(files) {
    for (const file of Array.from(files)) {
      if (uploads.length >= limits.maxModels) { notify('上传队列已满，请先清除已完成记录。', true); break; }
      const error = fileError(file, limits);
      const epoch = authEpoch, uid = expectedUID;
      const task = createUploadTask(file, $('ysm-upload-visibility').value, {onChange: updateUpload, onUnauthorized: loginRequired, onAccountChanged: accountChanged, expectedUID: uid, isCurrent: () => authenticated && epoch === authEpoch && uid === expectedUID});
      if (error) { task.state = 'error'; task.error = error; }
      const item = node('li', 'ysm-item'), main = node('div', 'ysm-item-main'), actions = node('div', 'ysm-item-actions');
      main.append(node('h3', '', file.name), node('p', 'ysm-meta', size(file.size)));
      const status = node('p', 'ysm-state'), progress = node('progress', 'ysm-progress'); progress.max = 100;
      progress.setAttribute('aria-label', file.name + ' 上传进度'); main.append(status, progress);
      const visibility = visibilitySelect(task.visibility); visibility.setAttribute('aria-label', file.name + ' 可见性');
      visibility.addEventListener('change', () => task.setVisibility(visibility.value));
      const cancel = button('取消', () => task.cancel());
      const retry = button('重试', () => runQueue([task]), !!error); if (error) retry.hidden = true;
      actions.append(visibility, cancel, retry); item.append(main, actions);
      rows.set(task, {item, status, progress, visibility, cancel, retry, invalid: !!error}); uploads.push(task);
      $('ysm-upload-list').append(item); updateUpload(task); if (error) { retry.hidden = true; visibility.disabled = true; }
    }
  }
  async function runQueue(tasks) {
    if (queueRunning || !authenticated) return;
    queueRunning = true; uploads.forEach(updateUpload); const epoch = authEpoch;
    try {
      for (const task of tasks) {
        if (!authenticated || epoch !== authEpoch) break;
        if (rows.get(task).invalid || task.state === 'complete') continue;
        // A queued task cancelled while another file uploads must remain cancelled.
        if (tasks.length > 1 && task.state !== 'queued') continue;
        await task.start();
      }
      if (authenticated && epoch === authEpoch) await refreshAll();
    } catch (error) { if (error.status !== 401) notify(error.message, true); }
    finally { queueRunning = false; uploads.forEach(task => { updateUpload(task); if (rows.get(task).invalid) rows.get(task).retry.hidden = true; }); }
  }
  function confirmAction(title, text) {
    const dialog = $('ysm-confirm'); if (dialog.open) return Promise.resolve(false);
    $('ysm-confirm-title').textContent = title; $('ysm-confirm-text').textContent = text;
    if (typeof dialog.showModal !== 'function') return Promise.resolve(window.confirm(title + '\n' + text));
    return new Promise(resolve => {
      function finish(value) {
        $('ysm-confirm-ok').removeEventListener('click', ok); $('ysm-confirm-cancel').removeEventListener('click', cancel);
        dialog.removeEventListener('cancel', escape); if (dialog.open) dialog.close(); resolve(value);
      }
      const ok = () => finish(true), cancel = () => finish(false), escape = event => { event.preventDefault(); finish(false); };
      $('ysm-confirm-ok').addEventListener('click', ok); $('ysm-confirm-cancel').addEventListener('click', cancel);
      dialog.addEventListener('cancel', escape); dialog.showModal();
    });
  }
  async function mutate(model, action, options = {}) {
    if (!authenticated || !expectedUID) return;
    const epoch = authEpoch;
    try {
      const result = await api(BASE + '/' + encodeURIComponent(model.id) + action, options);
      if (epoch !== authEpoch) return;
      if (action === '/select') notify('选用请求已确认。');
      else notify('模型设置已保存。');
      await refreshAll(); return result;
    } catch (error) {
      if (error.status !== 401 && authenticated) {
        notify(error.message + ' 正在核对服务器状态；请确认列表后再操作。', true);
        try { await refreshAll(); } catch (_) {}
      }
    }
  }
  async function download(model) {
    if (!authenticated || !expectedUID) return;
    try {
      // Fetch uses the same cookie / native original Bearer path as every other account request.
      const response = await fetch(BASE + '/' + encodeURIComponent(model.id) + '/download', {credentials: 'same-origin', cache: 'no-store', headers: {'X-Muxi-Expected-UID': expectedUID}});
      if (!response.ok) {
        if (response.status === 401) loginRequired();
        let body = {}; try { body = await response.json(); } catch (_) {}
        if (response.status === 409 && isAccountChanged(body)) accountChanged();
        throw new Error(detail(body, '文件暂时无法下载。'));
      }
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = node('a');
      link.href = url; link.download = model.name || 'model.ysm'; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) { notify(error.message, true); }
  }
  function modelRow(model, own) {
    const item = node('li', 'ysm-item'), main = node('div', 'ysm-item-main'), actions = node('div', 'ysm-item-actions');
    main.append(node('h3', '', model.name || '未命名模型'));
    main.append(node('p', 'ysm-meta', size(model.size) + ' · ' + (model.visibility === 'public' ? '公开' : '私有') + (model.deletedAt ? ' · 已删除（可恢复）' : '')));
    const status = node('p', 'ysm-state', (validationLabels[model.validationStatus] || '状态待确认') + ' · ' + (syncLabels[model.syncStatus] || '同步状态待确认'));
    status.classList.toggle('is-error', model.validationStatus === 'rejected'); main.append(status);
    if (model.validationStatus === 'pending') main.append(node('p', 'ysm-note', '文件在等待验证，当前无法下载或选用。'));
    if (own && model.visibility === 'public' && model.validationStatus !== 'verified') main.append(node('p', 'ysm-note', '已保存公开意愿；验证通过前不会展示在公共目录。'));
    if (model.deletedAt) {
      if (own) actions.append(button('恢复为私有', () => mutate(model, '/restore', {method: 'POST'}), model.canRestore === false));
    } else {
      if (own) {
        const visibility = visibilitySelect(model.visibility); visibility.setAttribute('aria-label', model.name + ' 可见性');
        visibility.addEventListener('change', async () => {
          const requested = visibility.value; visibility.value = model.visibility; visibility.disabled = true;
          const text = requested === 'private' ? '改为私有后会撤销他人的选用权限；服务器收到更新后将回退已穿戴模型。已下载的副本无法召回。' : '确认公开此模型？验证通过后所有人都可以看到和下载；服务器同步完成后可以选用。';
          try {
            if (await confirmAction(requested === 'private' ? '改为私有' : '公开模型', text)) await mutate(model, '/visibility', {method: 'PATCH', body: JSON.stringify({visibility: requested})});
          } finally { visibility.disabled = false; }
        }); actions.append(visibility);
      }
      actions.append(button(model.id === selectedModelId ? '已选用' : '选用', () => mutate(model, '/select', {method: 'POST'}), !authenticated || !canUse(model) || model.id === selectedModelId));
      actions.append(button('下载', () => download(model), !authenticated || !canDownload(model)));
      if (own) actions.append(button('删除', async () => {
        if (await confirmAction('删除模型', '删除“' + model.name + '”后会撤销可见和选用权限，保留期内可以恢复。')) await mutate(model, '', {method: 'DELETE'});
      }));
    }
    item.append(main, actions); return item;
  }
  function renderMine() {
    const visible = models.filter(model => !model.deletedAt || $('ysm-show-deleted').checked);
    $('ysm-my-list').replaceChildren(...visible.map(model => modelRow(model, true)));
    $('ysm-my-empty').hidden = visible.length > 0;
    const selected = models.find(model => model.id === selectedModelId);
    $('ysm-selected').textContent = selectedModelId ? '当前选用：' + (selected ? selected.name : '公共模型') : '当前未选用上传模型。';
  }
  let publicModels = [];
  function renderPublic() {
    $('ysm-public-list').replaceChildren(...publicModels.map(model => modelRow(model, false)));
    $('ysm-public-empty').hidden = publicModels.length > 0;
  }
  async function refreshMine() {
    const current = ++listEpoch, epoch = authEpoch;
    const profile = await api('/api/v1/player/profile');
    if (current !== listEpoch || epoch !== authEpoch) return;
    let uid;
    try { uid = profileUID(profile); }
    catch (error) {
      loginRequired(); expectedUID = null; uploads.length = 0; rows.clear(); $('ysm-upload-list').replaceChildren();
      throw error;
    }
    if (expectedUID && expectedUID !== uid) { accountChanged(); throw new Error('账户已切换，请重新登录或刷新页面。'); }
    expectedUID = uid;
    const result = await api(BASE);
    if (current !== listEpoch || epoch !== authEpoch) return;
    models = Array.isArray(result.models) ? result.models : [];
    selectedModelId = result.selectedModelId || null;
    if (result.limits) limits = {...DEFAULT_LIMITS, ...result.limits};
    $('ysm-limits').textContent = '每文件最多 ' + size(limits.maxFileBytes) + ' · 总容量 ' + size(limits.maxUserBytes) + ' · 最多 ' + limits.maxModels + ' 个模型';
    authenticated = true; $('ysm-private-content').hidden = false; $('ysm-login').hidden = true; renderMine(); renderPublic();
  }
  async function refreshPublic() {
    const current = ++publicEpoch;
    try {
      const result = await api('/api/v1/ysm/models/public'); if (current !== publicEpoch) return;
      publicModels = Array.isArray(result.models) ? result.models : []; renderPublic(); $('ysm-public-message').textContent = '';
    } catch (error) { $('ysm-public-message').textContent = error.message; }
  }
  async function refreshAll() { await Promise.all([refreshMine(), refreshPublic()]); }
  $('ysm-files').addEventListener('change', event => { addFiles(event.target.files); event.target.value = ''; });
  const drop = $('ysm-drop');
  ['dragenter', 'dragover'].forEach(event => drop.addEventListener(event, e => { e.preventDefault(); drop.classList.add('is-dragging'); }));
  drop.addEventListener('dragleave', e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove('is-dragging'); });
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('is-dragging'); if (authenticated) addFiles(e.dataTransfer.files); });
  $('ysm-upload-start').addEventListener('click', () => runQueue(uploads.filter(task => task.state === 'queued')));
  $('ysm-upload-clear').addEventListener('click', () => {
    for (let i = uploads.length - 1; i >= 0; i--) if (uploads[i].state !== 'queued' && !activeStates.includes(uploads[i].state)) {
      rows.get(uploads[i]).item.remove(); rows.delete(uploads[i]); uploads.splice(i, 1);
    }
  });
  $('ysm-show-deleted').addEventListener('change', renderMine);
  $('ysm-refresh').addEventListener('click', () => refreshAll().catch(error => { if (error.status !== 401) notify(error.message, true); }));
  $('ysm-public-refresh').addEventListener('click', refreshPublic);
  refreshAll().catch(error => { if (error.status !== 401) notify(error.message, true); });
})();
