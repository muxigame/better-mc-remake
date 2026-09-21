/* ============================================================
   BatterMC5Remake 启动器 — 前端
   Tauri 负责窗口与 IPC，游戏逻辑运行在 .NET sidecar。
   ============================================================ */

'use strict';

const $ = (id) => document.getElementById(id);
const tauri = window.__TAURI__;
const invoke = tauri && tauri.core && tauri.core.invoke;

let state = {};
let busy = false;
let optionalItems = [];

/* ───────────────────────── RPC ───────────────────────── */

let nextId = 1;
const pending = new Map();

function rpc(method, params) {
  const local = {
    minimize: 'minimize',
    close: 'close_app',
    beginDrag: 'begin_drag'
  };
  if (local[method]) return invoke(local[method]);

  return new Promise((resolve, reject) => {
    if (!invoke) { reject(new Error('没有可用的 Tauri 消息通道')); return; }
    const id = nextId++;
    pending.set(id, { resolve, reject });
    transportReady
      .then(() => invoke('send_message', { json: JSON.stringify({ id, method, params: params || {} }) }))
      .catch((error) => {
        pending.delete(id);
        reject(error instanceof Error ? error : new Error(String(error)));
      });
  });
}

function receive(msg) {
  if (!msg) return;

  if (msg.event) { handleEvent(msg.event, msg.payload || {}); return; }

  const slot = pending.get(msg.id);
  if (!slot) return;
  pending.delete(msg.id);
  if (msg.ok) slot.resolve(msg.result);
  else slot.reject(new Error(msg.error || '未知错误'));
}

const transportReady = tauri
  ? Promise.all([
      tauri.event.listen('backend-message', (e) => {
        try { receive(JSON.parse(e.payload)); }
        catch (error) { appendLog('ERROR', 'sidecar 消息解析失败：' + error); }
      }),
      tauri.event.listen('backend-error', (e) => appendLog('ERROR', String(e.payload || 'sidecar 异常退出')))
    ])
  : Promise.reject(new Error('Tauri API 不可用'));

/* ───────────────────────── 事件 ───────────────────────── */

function handleEvent(name, p) {
  switch (name) {
    case 'status':
      setProgress(p.phase, p.detail, p.fraction);
      break;

    case 'busy':
      setBusy(p.busy);
      break;

    case 'log':
      appendLog(p.level, p.message);
      break;

    case 'gameStarted':
      $('hero-meta').textContent = 'Minecraft 正在启动';
      break;

    case 'routeSelected':
      $('hero-meta').textContent = `${routeLabel(p.kind)} · ${Math.round(p.latencyMs || 0)} ms`;
      appendLog('INFO', `已选线路：${routeLabel(p.kind)} ${p.remote} → ${p.local}`);
      break;

    case 'gameWindowReady':
      setProgress('游戏已启动', '窗口已出现', 1);
      if (!(state.settings || {}).keepLauncherOpen) invoke('minimize').catch(() => {});
      break;

    case 'gameExited':
      invoke('restore_window').catch(() => {});
      onGameExited(p);
      break;

    case 'launcherUpdate':
      showLauncherUpdate(p);
      break;

    case 'state':
      render(p);
      break;
  }
}

function routeLabel(kind) {
  return ({
    LanDirect: '局域网直连',
    Ipv6Direct: 'IPv6 直连',
    Ipv4Direct: 'IPv4 直连',
    PortMapped: '路由器映射',
    UdpTunnel: 'UDP/QUIC 打洞',
    TcpTunnel: 'TCP 备用线路',
    Relay: '国内中继',
    Auto: '自动线路'
  })[kind] || kind || '自动线路';
}

function onGameExited(p) {
  setBusy(false);
  if (p.code === 0) {
    $('hero-meta').textContent = '游戏已退出';
    toast('游戏正常退出', 'good');
  } else {
    $('hero-meta').textContent = '游戏异常退出（退出码 ' + p.code + '）';
    toast(p.hint || ('游戏异常退出，退出码 ' + p.code), 'error', 12000);
    openLog();
  }
}

/* ───────────────────────── 界面状态 ───────────────────────── */

function setBusy(value) {
  busy = value;
  $('idle').hidden = value;
  $('working').hidden = !value;
  $('btn-play').disabled = value;
  $('btn-account-login').disabled = value;
  $('btn-account-register').disabled = value;
  $('btn-install-pack').disabled = value;
  $('btn-download-cancel').hidden = !value;
  $('download-progress').hidden = !value;
}

function setProgress(phase, detail, fraction) {
  $('progress-phase').textContent = phase || '';
  $('progress-detail').textContent = detail || '';

  const fill = $('progress-fill');
  if (typeof fraction === 'number' && fraction >= 0) {
    fill.classList.remove('indeterminate');
    fill.style.width = Math.round(Math.min(1, fraction) * 100) + '%';
  } else {
    fill.classList.add('indeterminate');
  }

  $('download-progress-phase').textContent = phase || '';
  $('download-progress-detail').textContent = detail || '';
  const downloadFill = $('download-progress-fill');
  if (typeof fraction === 'number' && fraction >= 0) {
    downloadFill.classList.remove('indeterminate');
    downloadFill.style.width = Math.round(Math.min(1, fraction) * 100) + '%';
  } else {
    downloadFill.classList.add('indeterminate');
  }
}

function toast(text, kind, ms) {
  const el = $('toast');
  $('toast-text').textContent = text;
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms || 5200);
}

function appendLog(level, message) {
  const body = $('logbody');
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;

  const line = document.createElement('div');
  line.className = 'l-' + level;
  line.textContent = message;
  body.appendChild(line);

  while (body.childElementCount > 600) body.removeChild(body.firstChild);
  if (atBottom) body.scrollTop = body.scrollHeight;
}

function openLog() { $('logdrawer').hidden = false; }

/* ───────────────────────── 渲染 ───────────────────────── */

function render(s) {
  state = s || {};
  const st = state.settings || {};

  const account = state.account;
  $('account-login').hidden = !!account;
  $('account-ready').hidden = !account;
  $('account-name').textContent = account ? account.username : '—';
  $('win-w').value = st.windowWidth || 1280;
  $('win-h').value = st.windowHeight || 720;
  $('fullscreen').checked = !!st.fullscreen;
  $('autojoin').checked = !!st.autoJoinServer;
  $('keepopen').checked = !!st.keepLauncherOpen;
  $('skipverify').checked = !!st.skipVerify;
  $('update-url').value = st.updateBaseUrl || '';
  $('auth-url').value = st.authBaseUrl || '';
  $('jvmargs').value = st.extraJvmArgs || '';

  const mem = st.maxMemoryMb || 0;
  $('memory-range').max = Math.max(16384, state.totalMemoryMb || 16384);
  $('memory-range').value = mem;
  $('memory-input').value = mem || '';
  updateMemoryDesc();

  $('java-current').textContent = st.javaPath || '自动选择';

  const pack = state.pack;
  if (pack) {
    $('hero-meta').textContent = pack.minecraft + ' · ' + pack.loader;
    $('status-pack').textContent = pack.name + ' ' + pack.version;
    $('about-pack').textContent = pack.name + ' ' + pack.version;
    $('about-mc').textContent = pack.minecraft;
    $('about-loader').textContent = pack.loader;
    $('about-files').textContent = pack.fileCount + ' 个';
    $('about-overlays').textContent = pack.overlayCount + ' 个文件';
    $('download-pack-name').textContent = pack.name;
    $('download-version').textContent = pack.version;
    $('download-size').textContent = humanSize(pack.size);
    $('download-files').textContent = pack.fileCount + ' 个';

    if (pack.notice) { $('about-notice').textContent = pack.notice; $('about-notice').hidden = false; }
  }

  const installed = state.installedVersion;
  if (installed && pack && installed === pack.version) $('status-sync').textContent = '已是最新';
  else if (installed) $('status-sync').textContent = '本地 ' + installed + '，待更新';
  else $('status-sync').textContent = '尚未安装';

  $('download-installed').textContent = installed || '未安装';
  if (installed && pack && installed === pack.version) {
    $('download-state').textContent = '已安装最新版本';
    $('btn-install-pack').textContent = '检查并修复';
  } else if (installed) {
    $('download-state').textContent = '发现可用更新';
    $('btn-install-pack').textContent = '更新整合包';
  } else {
    $('download-state').textContent = '尚未安装';
    $('btn-install-pack').textContent = '下载并安装';
  }

  const primary = (state.servers || []).find((x) => x.primary);
  $('autojoin-desc').textContent = primary
    ? '跳过多人列表，直连 ' + primary.host + ':' + primary.port
    : '跳过多人列表，直连主服务器';

  $('about-launcher').textContent = state.launcherVersion || '—';
  $('about-root').textContent = state.root || '—';
  $('about-gamedir').textContent = state.gameDir || '—';
  $('about-ram').textContent = state.totalMemoryMb ? (state.totalMemoryMb + ' MB') : '—';

  if (state.pack) $('java-hint').textContent =
    '本整合包需要 Java ' + state.pack.javaMajor + '。找不到会自动下载一份，不影响你机器上原有的 Java。';

  renderOptional(state.optional || [], st.enabledOptional || []);
  if (state.launcherUpdate) showLauncherUpdate(state.launcherUpdate);
  setBusy(!!state.busy);
}

function updateMemoryDesc() {
  const v = parseInt($('memory-input').value, 10);
  $('memory-desc').textContent = (!v || v <= 0)
    ? '自动（当前会分配 ' + (state.autoMemoryMb || '—') + ' MB）'
    : '固定 ' + v + ' MB';
}

function humanSize(bytes) {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)) + ' ' + units[i];
}

function renderOptional(items, enabled) {
  optionalItems = items;
  const box = $('optional-list');
  box.innerHTML = '';

  if (!items.length) {
    box.innerHTML = '<div class="list-empty">暂无可选内容</div>';
    return;
  }

  // 按组归拢，一组里的文件通常一起开关
  const groups = {};
  items.forEach((it) => { (groups[it.group] = groups[it.group] || []).push(it); });

  Object.keys(groups).forEach((g) => {
    const head = document.createElement('div');
    head.className = 'list-item';
    head.style.background = 'rgba(0,0,0,.25)';
    head.innerHTML = '<div class="li-main"><div class="li-title">' + escapeHtml(g)
      + '</div><div class="li-sub">' + groups[g].length + ' 个可选包 · '
      + humanSize(groups[g].reduce((a, b) => a + (b.size || 0), 0)) + '</div></div>';
    box.appendChild(head);

    groups[g].forEach((it) => {
      const row = document.createElement('label');
      row.className = 'list-item';
      row.style.cursor = 'pointer';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = typeof it.enabled === 'boolean'
        ? it.enabled
        : (it.paths || [it.path]).every((p) => enabled.indexOf(p) >= 0);
      cb.addEventListener('change', onOptionalToggle);
      cb.dataset.index = String(optionalItems.indexOf(it));

      const main = document.createElement('div');
      main.className = 'li-main';
      main.innerHTML = '<div class="li-title">' + escapeHtml(it.label) + '</div>'
        + '<div class="li-sub">' + escapeHtml(it.path) + '</div>';

      const size = document.createElement('span');
      size.className = 'badge';
      size.textContent = humanSize(it.size);

      row.appendChild(cb);
      row.appendChild(main);
      row.appendChild(size);
      box.appendChild(row);
    });
  });
}

function onOptionalToggle() {
  const enabled = Array.from($('optional-list').querySelectorAll('input[type=checkbox]'))
    .filter((c) => c.checked)
    .flatMap((c) => {
      const item = optionalItems[parseInt(c.dataset.index, 10)];
      return item ? (item.paths || [item.path]) : [];
    });
  save({ enabledOptional: enabled });
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showLauncherUpdate(u) {
  $('lu-version').textContent = u.version || '';
  $('lu-notes').textContent = u.notes || '';
  $('launcher-update').hidden = false;
  if (u.mandatory) toast('启动器必须更新到 ' + u.version + ' 才能进游戏', 'error', 15000);
}

/* ───────────────────────── 保存 ───────────────────────── */

let saveTimer = null;
let savePatch = {};

function save(patch) {
  Object.assign(savePatch, patch);
  clearTimeout(saveTimer);
  // 拖滑块会连发几十次，合并成一次写盘
  saveTimer = setTimeout(() => {
    const p = savePatch;
    savePatch = {};
    rpc('saveSettings', p).then(render).catch((e) => toast(e.message, 'error'));
  }, 220);
}

/* ───────────────────────── 启动 ───────────────────────── */

async function play() {
  try {
    setBusy(true);
    setProgress('准备中', '', -1);
    await rpc('launch', {});
  } catch (e) {
    setBusy(false);
    toast(e.message, 'error', 14000);
    openLog();
  }
}

async function accountLogin() {
  const error = $('account-error');
  try {
    $('btn-account-login').disabled = true;
    const result = await rpc('accountLogin', {});
    error.hidden = true;
    render(result);
    toast('Muxi Account 登录成功', 'good');
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  } finally {
    $('btn-account-login').disabled = false;
  }
}

async function accountRegister() {
  const error = $('account-error');
  try {
    $('btn-account-register').disabled = true;
    const result = await rpc('accountRegister', {});
    error.hidden = true;
    render(result);
    toast('Muxi Account 注册并登录成功', 'good');
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  } finally {
    $('btn-account-register').disabled = false;
  }
}

async function installPack() {
  try {
    setBusy(true);
    setProgress('准备下载', '', -1);
    const result = await rpc('install', {});
    render(result);
    toast('整合包已经准备就绪', 'good');
  } catch (e) {
    setBusy(false);
    toast(e.message, 'error', 14000);
    openLog();
  }
}

/* ───────────────────────── 绑定 ───────────────────────── */

function bind() {
  // 无边框窗口拖动：标题栏空白处按下就交给系统
  $('titlebar').addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.win-btn')) return;
    rpc('beginDrag');
  });

  $('btn-min').onclick = () => rpc('minimize');
  $('btn-close').onclick = () => rpc('close');

  $('btn-play').onclick = play;
  $('btn-account-login').onclick = accountLogin;
  $('btn-account-register').onclick = accountRegister;
  $('btn-account-logout').onclick = () => rpc('accountLogout').then(render).catch((e) => toast(e.message, 'error'));

  $('btn-cancel').onclick = () => rpc('cancel').catch(() => {});

  $('btn-log').onclick = () => { $('logdrawer').hidden = !$('logdrawer').hidden; };
  $('btn-log-close').onclick = () => { $('logdrawer').hidden = true; };

  $('btn-download-page').onclick = () => { $('downloads').hidden = false; };
  $('btn-download-close').onclick = () => { $('downloads').hidden = true; };
  $('downloads').addEventListener('mousedown', (e) => {
    if (e.target === $('downloads') && !busy) $('downloads').hidden = true;
  });
  $('btn-install-pack').onclick = installPack;
  $('btn-download-cancel').onclick = () => rpc('cancel').catch(() => {});

  // ── 设置开合 ──
  $('btn-settings').onclick = () => { $('settings').hidden = false; };
  $('btn-settings-close').onclick = () => { $('settings').hidden = true; };
  $('settings').addEventListener('mousedown', (e) => {
    if (e.target === $('settings')) $('settings').hidden = true;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('downloads').hidden && !busy) $('downloads').hidden = true;
    else if (!$('settings').hidden) $('settings').hidden = true;
    else if (!$('logdrawer').hidden) $('logdrawer').hidden = true;
  });

  $('panel-nav').addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-item');
    if (!btn) return;
    document.querySelectorAll('.nav-item').forEach((n) => n.classList.toggle('is-active', n === btn));
    document.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('is-active', t.dataset.tab === btn.dataset.tab));
    if (btn.dataset.tab === 'java') scanJava();
  });

  // ── 游戏 ──
  $('memory-range').addEventListener('input', () => {
    $('memory-input').value = $('memory-range').value === '0' ? '' : $('memory-range').value;
    updateMemoryDesc();
    save({ maxMemoryMb: parseInt($('memory-range').value, 10) || 0 });
  });
  $('memory-input').addEventListener('change', () => {
    const v = parseInt($('memory-input').value, 10) || 0;
    $('memory-range').value = Math.min(v, parseInt($('memory-range').max, 10));
    updateMemoryDesc();
    save({ maxMemoryMb: v });
  });

  $('win-w').addEventListener('change', () => save({ windowWidth: parseInt($('win-w').value, 10) }));
  $('win-h').addEventListener('change', () => save({ windowHeight: parseInt($('win-h').value, 10) }));
  $('fullscreen').addEventListener('change', () => save({ fullscreen: $('fullscreen').checked }));
  $('autojoin').addEventListener('change', () => save({ autoJoinServer: $('autojoin').checked }));
  $('keepopen').addEventListener('change', () => save({ keepLauncherOpen: $('keepopen').checked }));

  // ── Java ──
  $('btn-java-scan').onclick = scanJava;
  $('btn-java-browse').onclick = () =>
    invoke('pick_java')
      .then((path) => {
        if (!path) return;
        return rpc('saveSettings', { javaPath: path }).then((s) => {
          render(s);
          toast('已选择 Java', 'good');
          scanJava();
        });
      })
      .catch((e) => toast(String(e), 'error'));
  $('btn-java-auto').onclick = () => { save({ javaPath: null }); toast('已改回自动选择', 'good'); };

  // ── 更新 ──
  $('update-url').addEventListener('change', () => save({ updateBaseUrl: $('update-url').value.trim() }));
  $('auth-url').addEventListener('change', () => save({ authBaseUrl: $('auth-url').value.trim() }));
  $('skipverify').addEventListener('change', () => save({ skipVerify: $('skipverify').checked }));
  $('btn-reverify').onclick = () =>
    rpc('resetVerification')
      .then(() => toast('已清空校验缓存，下次启动会逐个文件核对', 'good'))
      .catch((e) => toast(e.message, 'error'));
  $('btn-launcher-update').onclick = () =>
    rpc('applyLauncherUpdate').catch((e) => toast(e.message, 'error'));

  // ── 高级 ──
  $('jvmargs').addEventListener('change', () => save({ extraJvmArgs: $('jvmargs').value }));
  document.querySelectorAll('[data-open]').forEach((b) => {
    b.onclick = () => rpc('openPath', { which: b.dataset.open }).catch((e) => toast(e.message, 'error'));
  });
}

function scanJava() {
  const box = $('java-list');
  box.innerHTML = '<div class="list-empty">正在扫描…</div>';

  rpc('detectJava').then((r) => {
    const items = r.items || [];
    const need = state.pack ? state.pack.javaMajor : 21;
    const current = (state.settings || {}).javaPath;

    if (!items.length) {
      box.innerHTML = '<div class="list-empty">没有找到任何 Java。直接点开始游戏，会自动下载一份。</div>';
      return;
    }

    box.innerHTML = '';
    items.forEach((j) => {
      const row = document.createElement('div');
      row.className = 'list-item' + (current === j.path ? ' is-current' : '');

      const main = document.createElement('div');
      main.className = 'li-main';
      main.innerHTML = '<div class="li-title">Java ' + escapeHtml(j.version)
        + ' <span style="color:var(--muted)">· ' + escapeHtml(j.source) + '</span></div>'
        + '<div class="li-sub">' + escapeHtml(j.path) + '</div>';

      const badge = document.createElement('span');
      badge.className = 'badge' + (j.major === need ? ' ok' : '');
      badge.textContent = j.major === need ? '可用' : ('Java ' + j.major);

      const pick = document.createElement('button');
      pick.className = 'mc-btn mc-btn-small';
      pick.textContent = current === j.path ? '使用中' : '选择';
      pick.disabled = current === j.path;
      pick.onclick = () => {
        save({ javaPath: j.path });
        toast('已选择 Java ' + j.version, 'good');
        setTimeout(scanJava, 320);
      };

      row.appendChild(main);
      row.appendChild(badge);
      row.appendChild(pick);
      box.appendChild(row);
    });
  }).catch((e) => {
    box.innerHTML = '<div class="list-empty">扫描失败：' + escapeHtml(e.message) + '</div>';
  });
}

function refresh() {
  return rpc('getState').then(render).catch((e) => toast(e.message, 'error'));
}

/* ───────────────────────── 启动 ───────────────────────── */

bind();
rpc('init')
  .then((s) => {
    render(s);
    if (!s.account) $('btn-account-login').focus();
  })
  .catch((e) => {
    $('hero-meta').textContent = '初始化失败';
    toast(e.message, 'error', 20000);
  });
