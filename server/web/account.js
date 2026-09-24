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
  // ── 崩溃日志 ──
  // 启动器在游戏异常退出后上传。编号是给玩家发给管理员用的，所以显眼、可选中复制。
  const FILE_LABELS = {
    'report.json': '摘要', 'crash-report.txt': '崩溃报告', 'latest.log': '游戏日志',
    'game-output.log': '游戏输出', 'launcher.log': '启动器日志', 'hs_err.log': 'JVM 崩溃', 'environment.json': '电脑环境',
  };
  const FILE_ORDER = ['crash-report.txt', 'latest.log', 'hs_err.log', 'game-output.log', 'launcher.log', 'environment.json', 'report.json'];
  const size = bytes => bytes < 1024 ? bytes + ' B' : bytes < 1048576 ? (bytes / 1024).toFixed(1) + ' KB' : (bytes / 1048576).toFixed(1) + ' MB';
  const when = seconds => new Date(seconds * 1000).toLocaleString('zh-CN', { hour12: false });
  function crashMessage(text, error = false) {
    $('crash-message').textContent = text;
    $('crash-message').classList.toggle('is-error', error);
    $('crash-message').hidden = !text;
  }
  function crashItem(report, focus) {
    const li = document.createElement('li');
    li.className = 'pc-crash-item' + (focus ? ' is-focus' : '');
    li.id = 'crash-' + report.id;
    const main = document.createElement('div');
    main.className = 'pc-crash-main';
    const reason = document.createElement('span');
    reason.className = 'pc-crash-reason';
    // 反馈看玩家写的话；崩溃看崩溃报告里摘出来的那句（Description + 异常），没有才用启动器的判断
    const isCrash = report.kind === 'crash';
    reason.textContent = isCrash
      ? report.summary || report.reason || '游戏异常退出'
      : (report.message || '').split('\n')[0] || '意见反馈';
    reason.title = (isCrash ? [report.summary, report.reason] : [report.message]).filter(Boolean).join('\n')
      || reason.textContent;
    const meta = document.createElement('span');
    meta.className = 'pc-crash-meta';
    const parts = [
      ['编号 ', report.id],
      [isCrash ? '崩溃日志' : '意见反馈'],
      [when(report.createdAt)],
      report.packVersion ? ['整合包 ' + report.packVersion] : null,
      isCrash && report.exitCode !== undefined ? ['退出码 ' + report.exitCode] : null,
      report.environmentIncluded ? ['含电脑环境'] : null,
      !isCrash && report.logsIncluded ? ['含运行日志'] : null,
      [size(report.size)],
    ].filter(Boolean);
    parts.forEach(([label, mono]) => {
      const span = document.createElement('span');
      if (mono) {
        span.append(label);
        const code = document.createElement('span');
        code.className = 'pc-mono';
        code.textContent = mono;
        span.append(code);
      } else span.textContent = label;
      meta.append(span);
    });
    main.append(reason, meta);
    const actions = document.createElement('div');
    actions.className = 'pc-crash-actions';
    const view = document.createElement('button');
    view.type = 'button';
    view.textContent = '查看';
    view.addEventListener('click', () => openCrash(report.id));
    const download = document.createElement('a');
    download.href = `/api/v1/player/crash-reports/${report.id}/download`;
    download.textContent = '下载';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'is-danger';
    remove.textContent = '删除';
    remove.addEventListener('click', async () => {
      if (!confirm(`删除崩溃日志 ${report.id}？删除后管理员也看不到了。`)) return;
      remove.disabled = true;
      try { await api(`/api/v1/player/crash-reports/${report.id}`, { method: 'DELETE' }); li.remove(); showEmpty(); }
      catch (error) { remove.disabled = false; crashMessage(error.message, true); }
    });
    actions.append(view, download, remove);
    li.append(main, actions);
    return li;
  }
  function showEmpty() { $('crash-empty').hidden = $('crash-list').children.length > 0; }
  async function showCrashFile(id, name, button) {
    document.querySelectorAll('#crash-files button').forEach(b => b.setAttribute('aria-selected', String(b === button)));
    $('crash-file-body').textContent = '正在读取…';
    $('crash-file-note').hidden = true;
    try {
      const response = await fetch(`/api/v1/player/crash-reports/${id}/files/${encodeURIComponent(name)}`,
        { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('读取失败');
      $('crash-file-body').textContent = await response.text();
      $('crash-file-note').hidden = response.headers.get('X-Truncated') !== '1';
    } catch (_) { $('crash-file-body').textContent = '暂时读不出这个文件，可以下载后查看。'; }
  }
  async function openCrash(id) {
    let report;
    try { report = (await api(`/api/v1/player/crash-reports/${id}`)).report; }
    catch (error) { crashMessage(error.message, true); return; }
    $('crash-dialog-title').textContent = id;
    $('crash-dialog-kicker').textContent = report.kind === 'crash' ? 'CRASH REPORT' : 'FEEDBACK';
    $('crash-dialog-message').textContent = report.message || '';
    $('crash-dialog-message').hidden = !report.message;
    const tabs = $('crash-files');
    tabs.textContent = '';
    const rank = name => { const i = FILE_ORDER.indexOf(name); return i < 0 ? FILE_ORDER.length : i; };
    const files = report.files.map(f => f.name).sort((a, b) => rank(a) - rank(b));
    files.forEach(name => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.textContent = FILE_LABELS[name] || name;
      button.title = name;
      button.addEventListener('click', () => showCrashFile(id, name, button));
      tabs.append(button);
    });
    $('crash-dialog').showModal();
    if (tabs.firstChild) tabs.firstChild.click();
  }
  async function loadCrashReports() {
    try {
      const { reports } = await api('/api/v1/player/crash-reports');
      const focus = decodeURIComponent(location.hash.replace(/^#crash-/, ''));
      $('crash-list').textContent = '';
      reports.forEach(report => $('crash-list').append(crashItem(report, report.id === focus)));
      showEmpty();
      $('crash-section').hidden = false;
      // 启动器上传完会带着 #crash-编号 打开这里，直接展开那一份。
      // 不在自己列表里的编号也试着打开：管理员从后台点过来看别人的；普通玩家会得到"没有"
      if (focus) {
        if (reports.some(r => r.id === focus)) $('crash-' + focus).scrollIntoView({ block: 'center' });
        openCrash(focus);
      }
    } catch (error) {
      if (error.status === 401) return;
      $('crash-section').hidden = false;
      crashMessage('暂时无法读取崩溃日志，请刷新页面重试。', true);
    }
  }
  $('crash-dialog-close').addEventListener('click', () => $('crash-dialog').close());

  async function load() {
    try { render(await api('/api/v1/player/profile')); loadCrashReports(); }
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
