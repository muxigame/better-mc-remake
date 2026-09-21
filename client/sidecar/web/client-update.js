/* Full-client updates use their own modal and never reuse pack/game progress. */
'use strict';

window.createClientUpdateDialog = function createClientUpdateDialog(deps) {
  const get = deps.get;
  const dialog = get('client-update-dialog');
  const activePhases = new Set(['checking', 'downloading', 'verifying', 'installing']);
  const shown = new Set();
  let release = null;
  let phase = 'idle';
  let error = '';
  let downloaded = 0;
  let total = 0;
  let startedAt = 0;
  let checking = false;
  let authoritativeVersion = null;
  let focusBefore = null;
  const size = (value) => value > 0 ? deps.humanSize(value) : '0 B';
  const inProgress = () => activePhases.has(phase);
  const required = () => !!(release && release.mandatory);
  const key = () => release ? `${release.version}:${required()}` : '';

  function render() {
    const active = inProgress();
    get('cu-title').textContent = ({ checking: '正在检查更新', downloading: '正在下载更新', verifying: '正在校验更新', installing: '正在启动安装器', error: '更新未完成' })[phase] || (required() ? '需要更新客户端' : '发现新版本');
    get('cu-description').textContent = required()
      ? (release.updateReason || '当前版本已停止支持，请更新后继续。')
      : active ? '完成后将自动重启客户端。' : '可立即更新，也可稍后继续。';
    get('cu-current').textContent = (release && release.currentVersion) || deps.currentVersion() || '—';
    get('cu-version').textContent = (release && release.version) || '—';
    get('cu-size').textContent = release && release.size ? size(release.size) : '';
    get('cu-notes').textContent = release && release.notes ? release.notes.trim() : '';
    get('cu-notes').hidden = !get('cu-notes').textContent || active;
    get('cu-error').textContent = error;
    get('cu-error').hidden = !error;
    get('cu-progress').hidden = !active;
    get('cu-close').hidden = required() || active;
    get('cu-close').disabled = required() || active;
    get('cu-later').textContent = required() ? '退出客户端' : '稍后';
    get('cu-later').disabled = active;
    get('cu-update').disabled = active || checking || deps.isAppBusy();
    get('cu-update').textContent = active ? (phase === 'installing' ? '正在安装…' : '更新中…') : phase === 'error' ? '重试' : '立即更新';
    get('cu-phase').textContent = ({ checking: '检查更新', downloading: '下载安装包', verifying: '校验签名', installing: '启动安装器' })[phase] || '';
    const complete = phase === 'verifying' || phase === 'installing';
    const fraction = complete ? 1 : total > 0 ? Math.min(1, downloaded / total) : null;
    const percent = fraction === null ? null : Math.floor(fraction * 100);
    get('cu-percent').textContent = percent === null ? '—' : `${percent}%`;
    get('cu-fill').classList.toggle('indeterminate', fraction === null);
    get('cu-fill').style.width = fraction === null ? '' : `${fraction * 100}%`;
    if (percent === null) get('cu-meter').removeAttribute('aria-valuenow');
    else get('cu-meter').setAttribute('aria-valuenow', String(percent));
    get('cu-bytes').textContent = total > 0 ? `${size(downloaded)} / ${size(total)}` : size(downloaded);
    const seconds = (Date.now() - startedAt) / 1000;
    get('cu-speed').textContent = phase === 'downloading' && seconds > 0.5 && downloaded > 0 ? `${size(downloaded / seconds)}/s` : '';
    get('btn-client-update-check').disabled = checking || active;
    deps.onStateChange();
  }

  function open(force = false) {
    if (!release && phase !== 'error') return;
    if (deps.isAppBusy() && !force) return; // Do not interrupt a running game/auth flow.
    if (!dialog.open) {
      focusBefore = document.activeElement;
      dialog.showModal();
      get('cu-update').focus();
    }
    if (release) shown.add(key());
    render();
  }

  function dismiss() {
    if (required() || inProgress()) return;
    dialog.close();
    if (focusBefore && focusBefore.isConnected) focusBefore.focus();
  }

  function offer(info, options = {}) {
    if (!info || !info.version) return;
    if (inProgress()) return; // Repeated backend state must not reset a download.
    // A repeated cached game manifest must not override a fresh native check.
    if (!options.authoritative && authoritativeVersion === info.version &&
        !(info.mandatory === true && release && !release.mandatory)) return;
    const changed = !release || release.version !== info.version || !!release.mandatory !== !!info.mandatory;
    release = { ...info, mandatory: info.mandatory === true };
    if (options.authoritative) authoritativeVersion = info.version;
    if (changed) { phase = 'idle'; error = ''; }
    get('launcher-update').hidden = false;
    get('lu-version').textContent = release.version;
    get('lu-notes').textContent = release.notes || '';
    get('client-update-status').textContent = required() ? '当前版本已停止支持' : `可更新至 ${release.version}`;
    render();
    if (options.force || !shown.has(key())) open(!!options.force);
  }

  async function check(manual = false) {
    if (checking || inProgress()) return;
    checking = true;
    render();
    try {
      const info = await deps.check();
      if (info) offer(info, { authoritative: true, force: manual });
      else {
        release = null;
        authoritativeVersion = null;
        phase = 'idle';
        error = '';
        if (dialog.open) dialog.close();
        get('launcher-update').hidden = true;
        get('client-update-status').textContent = '已是最新版本';
        if (manual) deps.notify('当前已是最新客户端');
      }
    } catch (e) {
      get('client-update-status').textContent = '暂时无法检查更新';
      if (manual) {
        phase = 'error';
        error = String(e && e.message ? e.message : e).slice(0, 500);
        open(true);
      }
      deps.log('WARN', '客户端更新检查失败：' + e);
    } finally { checking = false; render(); }
  }

  function progress(p) {
    if (!inProgress()) return;
    if (p.phase === 'started') {
      if (release && p.version) release.version = p.version;
      phase = 'downloading';
      startedAt = Date.now();
      downloaded = 0;
      total = (release && release.size) || 0;
    } else if (p.phase === 'downloading') {
      phase = 'downloading';
      downloaded = Math.max(downloaded, Number(p.downloaded) || 0);
      total = Number(p.total) || total;
    } else if (p.phase === 'verifying' || p.phase === 'installing') {
      phase = p.phase;
      if (total > 0) downloaded = total;
    }
    render();
  }

  async function start() {
    if (checking || inProgress() || deps.isAppBusy()) return;
    if (!release) { await check(true); return; }
    phase = 'checking'; error = ''; downloaded = 0; total = 0;
    open(true);
    let reserved = false;
    let failure = null;
    try {
      await deps.reserve();
      reserved = true;
      await deps.install(); // Windows exits only after successful installer handoff.
    } catch (e) {
      failure = e;
    } finally {
      if (reserved) await deps.release().catch(() => {});
      if (failure !== null) {
        phase = 'error';
        error = String(failure && failure.message ? failure.message : failure).slice(0, 500);
        render();
      }
    }
  }

  dialog.addEventListener('cancel', (e) => { e.preventDefault(); dismiss(); });
  get('cu-close').onclick = dismiss;
  get('cu-later').onclick = () => {
    if (inProgress()) return;
    if (required()) deps.exit(); else dismiss();
  };
  get('cu-update').onclick = start;
  get('btn-client-update-check').onclick = () => check(true);
  get('btn-launcher-update').onclick = () => release ? open(true) : check(true);

  return {
    offer, check, progress,
    isBusy: inProgress,
    blocksUse: () => required() || inProgress(),
    isOpen: () => dialog.open,
    refreshActivity() {
      render();
      if (release && !shown.has(key()) && !deps.isAppBusy()) open();
    },
  };
};
