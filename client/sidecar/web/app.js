/* ============================================================
   BetterMC5Remake 启动器 — 前端
   Tauri 负责窗口与 IPC，游戏逻辑运行在 .NET sidecar。
   ============================================================ */

'use strict';

const $ = (id) => document.getElementById(id);
const tauri = window.__TAURI__;
const invoke = tauri && tauri.core && tauri.core.invoke;

let state = {};
let busy = false;
let gameRunning = false;
let connecting = false;   // 后台还在连服务器（游戏已经照常启动）
let abortConfirm = null;
let accountAuthPending = false;
let packInstallPending = false;
let optionalItems = [];
let clientUpdate = null;

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
      tauri.event.listen('backend-error', (e) => appendLog('ERROR', String(e.payload || 'sidecar 异常退出'))),
      tauri.event.listen('client-update-progress', (e) => clientUpdate?.progress(e.payload || {}))
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
      // 游戏不再等连接，进程一起来就给出中止按钮；连接进度照样在进度条上走
      $('hero-meta').textContent = 'Minecraft 正在启动';
      setGameRunning(true);
      break;

    case 'routeConnecting':
      setConnecting(true);
      break;

    case 'routeSelected':
      setConnecting(false);
      $('hero-meta').textContent = `${routeLabel(p.kind)} · ${Math.round(p.latencyMs || 0)} ms`;
      appendLog('INFO', `已选线路：${routeLabel(p.kind)} ${p.remote} → ${p.local}`);
      if (gameRunning) setProgress('已连上服务器', `${routeLabel(p.kind)} · ${Math.round(p.latencyMs || 0)} ms`, 1);
      break;

    case 'routePending':
      // 连不上服务器不影响玩游戏：单机照玩，后台一直接着连，连上了会再来一条 routeSelected
      setConnecting(true);
      $('hero-meta').textContent = `${p.reason || '暂时连不上服务器'} · 后台重连中`;
      appendLog('WARN', `${p.reason || '暂时连不上服务器'}，游戏照常启动，后台继续连：${p.detail || ''}`);
      break;

    case 'gameWindowReady':
      if (!connecting) setProgress('游戏运行中', '启动器可以最小化，不影响游戏', 1);
      setGameRunning(true);
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

/*
   用实测到的显卡名字填下拉框。

   Windows 只认"高性能"和"节能"两档，不能按名字点名某块卡，所以这里是把这两档用
   真实的显卡名呈现出来——双显卡机器上两者等价。哪块卡算哪一档由 sidecar 定
   （见 GpuPreference.ChoiceFor），前端不自己猜。

   同一档位可能对应多块卡（核显 + 独显 + 外接显卡坞），只保留第一块，不然会出现两个
   选项点下去效果一样。
*/
/* 光影下拉。

   列表来自 sidecar 扫描的 shaderpacks 目录，空串代表关闭光影。
   当前值是 sidecar 从 iris.properties 里读出来的真实值，所以玩家在游戏里换完光影，
   启动器一刷新就同步过来，不需要在这边猜。
*/
/* 状态栏的组件状态点。

   安装是分三步的：整合包文件、Minecraft 本体、NeoForge。以前状态栏只有一个
   "已安装/尚未安装"，卡在哪一步看不出来。现在一个组件一个点，出问题一眼看到是哪块。

   ok=绿（装好且最新）  update=黄（能用，有新版）  missing=红（还没装）
*/
var COMPONENT_ORDER = ['pack', 'minecraft', 'loader', 'launcher'];
var COMPONENT_HINT = { ok: '已安装', update: '有可用更新', missing: '尚未安装' };

function renderComponents(components) {
  const box = $('status-components');
  if (!box) return;
  box.innerHTML = '';
  if (!components) return;

  COMPONENT_ORDER.forEach((key) => {
    const c = components[key];
    if (!c) return;

    const wrap = document.createElement('span');
    wrap.className = 'status-comp';
    const state = c.state === 'ok' || c.state === 'update' ? c.state : 'missing';
    wrap.title = c.label + '：' + (COMPONENT_HINT[state] || '');

    const led = document.createElement('span');
    led.className = 'status-led ' + state;
    wrap.appendChild(led);

    const text = document.createElement('span');
    text.textContent = c.version ? c.label + ' ' + c.version : c.label;
    wrap.appendChild(text);

    box.appendChild(wrap);
  });
}

function renderShaderOptions(shaders, current) {
  const sel = $('shaderpack');
  const list = shaders && Array.isArray(shaders.available) ? shaders.available : [];
  sel.innerHTML = '';

  const options = [{ value: '', label: '不使用光影' }];
  for (const name of list) options.push({ value: name, label: name });

  for (const o of options) {
    const el = document.createElement('option');
    el.value = o.value;
    el.textContent = o.label;
    sel.appendChild(el);
  }
  sel.value = options.some((o) => o.value === current) ? current : '';
}

// 服务端下发的公告。关掉之后按 id 记住，换文案要换 id，否则关过的人看不到新的。
// localStorage 在这里只是"少看一次"的便利：读不到、写不进都不该影响界面，
// 所以两头都包 try，拿不到就当没关过，照常显示。
function dismissedAnnouncement() {
  try { return localStorage.getItem('announce-dismissed') || ''; } catch (_) { return ''; }
}

function renderAnnouncement(notice) {
  const box = $('announce');
  if (!box) return;
  if (!notice || !(notice.title || notice.body) || dismissedAnnouncement() === notice.id) {
    box.hidden = true;
    return;
  }
  $('announce-title').textContent = notice.title || '';
  $('announce-body').textContent = notice.body || '';
  $('announce-body').hidden = !notice.body;
  box.dataset.level = notice.level || 'info';
  box.hidden = false;
}

function onAnnouncementClosed() {
  const notice = (state && state.announcement) || null;
  if (notice && notice.id) {
    try { localStorage.setItem('announce-dismissed', notice.id); } catch (_) { /* 无痕模式等，忽略 */ }
  }
  $('announce').hidden = true;
}

function renderGpuOptions(gpus, current) {
  const sel = $('gpu');
  const list = Array.isArray(gpus) ? gpus : [];
  const options = [];
  const taken = new Set();

  for (const gpu of list) {
    const value = gpu.choice === 'power' ? 'power' : 'performance';
    if (taken.has(value)) continue;
    taken.add(value);
    options.push({ value, label: gpu.name });
  }
  if (options.length === 0) options.push({ value: 'performance', label: '默认显卡' });
  options.push({ value: 'auto', label: '由 Windows 决定' });

  sel.innerHTML = '';
  for (const o of options) {
    const el = document.createElement('option');
    el.value = o.value;
    el.textContent = o.label;
    sel.appendChild(el);
  }
  // 存的是档位不是名字：换机器、换驱动名字都会变，档位不会。
  // 存的值在这台机器上没有对应选项时，退回第一个而不是留个空白。
  sel.value = options.some((o) => o.value === current) ? current : options[0].value;
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
  setConnecting(false);
  setGameRunning(false);
  setBusy(false);
  // 老 sidecar 没有 crashed 字段，按退出码兜底
  const crashed = p.crashed === undefined ? p.code !== 0 : !!p.crashed;
  if (!crashed) {
    $('hero-meta').textContent = '游戏已退出';
    toast('游戏正常退出', 'good');
  } else {
    $('hero-meta').textContent = p.code === 0 ? '游戏崩溃了' : '游戏异常退出（退出码 ' + p.code + '）';
    showCrash('crash', p);
  }
}

/* 游戏异常退出 / 上传日志。

   替代整合包原来带的 Crash Assistant：它的窗口按钮带倒计时关不掉，日志传到第三方，
   还夹着盗版提示和「模组被改过」提示。这里随时能关，玩家点了才上传，存到自己账号下。
   手动上传（设置 → 高级）也走这个框，只是没有崩溃原因。 */
let crashMode = 'crash';
let crashReportId = null;

function showCrash(mode, p) {
  crashMode = mode;
  crashReportId = null;
  const loggedIn = !!state.account;
  $('crash-title').textContent = mode === 'crash' ? '游戏异常退出' : '上传日志';
  $('crash-hint').textContent = mode === 'crash'
    ? (p && p.hint) || ('游戏异常退出，退出码 ' + (p ? p.code : '?'))
    : '把最近一次游戏和启动器的日志传到你的账号下，在玩家中心能看到。';
  $('crash-summary').textContent = (p && p.summary) || '';
  $('crash-summary').hidden = !(p && p.summary);
  $('crash-env').checked = (state.settings || {}).crashReportEnvironment !== false;
  $('crash-result').hidden = loggedIn;
  $('crash-result').classList.toggle('is-error', !loggedIn);
  $('crash-result').textContent = loggedIn ? '' : '登录 muxi 账户后才能上传，日志会存到你的账号下。';
  $('btn-crash-upload').disabled = !loggedIn;
  $('btn-crash-upload').textContent = '上传日志';
  $('btn-crash-view').hidden = true;
  $('crash').hidden = false;
}

function uploadCrashLogs() {
  const button = $('btn-crash-upload');
  button.disabled = true;
  button.textContent = '正在上传…';
  $('crash-result').hidden = true;
  const includeEnvironment = $('crash-env').checked;
  rpc('logUpload', { kind: crashMode, includeEnvironment })
    .then((r) => {
      crashReportId = r.id;
      // sidecar 已经把这次的选择记进设置了，本地这份跟上，下次打开弹窗不回跳
      if (state.settings) state.settings.crashReportEnvironment = includeEnvironment;
      button.textContent = '已上传';
      $('crash-result').classList.remove('is-error');
      $('crash-result').textContent = '已上传，编号 ' + r.id + '。找管理员帮忙时把编号发给他就行。';
      $('crash-result').hidden = false;
      $('btn-crash-view').hidden = false;
    })
    .catch((e) => {
      button.disabled = false;
      button.textContent = '重新上传';
      $('crash-result').classList.add('is-error');
      $('crash-result').textContent = e.message;
      $('crash-result').hidden = false;
    });
}

/* ───────────────────────── 界面状态 ───────────────────────── */

/*
   游戏进程一起来就给出中止按钮：后端的 cancel 本来就会 Kill 整棵进程树，这里只是把它接出来。
   进度条在游戏运行时只剩一个用处：显示后台连服务器的进度。连上了就收起来，
   还在连（包括连不上、后台一直重试）就一直挂着。
*/
function setGameRunning(value) {
  // 游戏起来时会连着来两次（gameStarted、gameWindowReady）。状态没变就别动按钮：
  // 玩家在这两次之间已经点了一下"中止游戏"的话，第二次会把那个待确认状态悄悄清掉。
  if (gameRunning === !!value) return;
  gameRunning = !!value;
  clearAbortConfirm();
  $('progress-track').hidden = gameRunning && !connecting;
  $('btn-cancel').classList.toggle('mc-btn-danger', gameRunning);
  $('btn-cancel').textContent = gameRunning ? '中止游戏' : '取消';
}

function setConnecting(value) {
  connecting = !!value;
  if (gameRunning) $('progress-track').hidden = !connecting;
}

function clearAbortConfirm() {
  if (!abortConfirm) return;
  clearTimeout(abortConfirm);
  abortConfirm = null;
}

/* 游戏在跑的时候误点一下就是丢掉没存的进度，所以要点第二次才真的动手。 */
function onCancelClicked() {
  if (gameRunning && !abortConfirm) {
    $('btn-cancel').textContent = '再点一次确认中止';
    abortConfirm = setTimeout(() => {
      abortConfirm = null;
      if (gameRunning) $('btn-cancel').textContent = '中止游戏';
    }, 4000);
    return;
  }
  clearAbortConfirm();
  rpc('cancel').catch(() => {});
}

function setBusy(value) {
  busy = !!value || packInstallPending;
  if (!busy && gameRunning) setGameRunning(false);
  $('idle').hidden = busy;
  $('working').hidden = !busy;
  syncClientActions();
  $('btn-download-cancel').hidden = !busy;
  $('download-progress').hidden = !busy;
  clientUpdate?.refreshActivity();
}

function syncClientActions() {
  const blocked = busy || !!clientUpdate?.blocksUse();
  $('btn-play').disabled = blocked;
  $('btn-install-pack').disabled = blocked;
  syncAccountAuthButtons();
}

function syncAccountAuthButtons() {
  const disabled = busy || accountAuthPending || !!clientUpdate?.blocksUse();
  $('btn-account-login').disabled = disabled;
  $('btn-account-register').disabled = disabled;
}

function setAccountAuthPending(value) {
  accountAuthPending = value;
  syncAccountAuthButtons();
  clientUpdate?.refreshActivity();
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
  if (packInstallPending) $('download-state').textContent = phase || '正在安装';
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
  $('account-name').textContent = account ? (account.nickname || account.username) : '—';
  $('win-w').value = st.windowWidth || 1280;
  $('win-h').value = st.windowHeight || 720;
  $('fullscreen').checked = !!st.fullscreen;
  $('autojoin').checked = !!st.autoJoinServer;
  $('keepopen').checked = !!st.keepLauncherOpen;
  $('skipverify').checked = !!st.skipVerify;
  renderAnnouncement(state.announcement);
  renderGpuOptions(state.gpus, st.gpu || 'performance');
  // 显示以 iris.properties 的实际值为准，设置里那份只是兜底
  renderShaderOptions(state.shaders, (state.shaders && state.shaders.current) || st.shaderPack || '');
  $('update-url').value = st.updateBaseUrl || '';
  $('auth-url').value = st.authBaseUrl || '';
  $('jvmargs').value = st.extraJvmArgs || '';

  const mem = st.maxMemoryMb || 0;
  // 上限由 sidecar 给：给系统留 2G，且不超过整合包声明的阈值。
  // 以前这里写死最低 16384，12G 的机器也能拉到 16G，进游戏必挨警告屏。
  const ceiling = state.memoryCeilingMb || state.totalMemoryMb || 8192;
  $('memory-range').max = ceiling;
  $('memory-input').max = ceiling;
  $('memory-range').value = Math.min(mem, ceiling);
  $('memory-input').value = mem || '';
  updateMemoryDesc();

  $('java-current').textContent = st.javaPath || '自动选择';

  const pack = state.pack;
  if (pack) {
    // 版本号都挪到状态栏了，这行留给线路和运行状态用（.hero-meta 有 min-height，不会跳）
    $('hero-meta').textContent = '';
    $('about-pack').textContent = 'BMC [Remake] ' + pack.version;
    $('about-mc').textContent = pack.minecraft;
    $('about-loader').textContent = pack.loader;
    $('about-files').textContent = pack.fileCount + ' 个';
    $('about-overlays').textContent = pack.overlayCount + ' 个文件';
    $('download-pack-name').textContent = pack.name;
    $('download-version').textContent = pack.version;
    $('download-size').textContent = humanSize(pack.size);
    $('download-files').textContent = pack.fileCount + ' 个';

    if (pack.notice) { $('about-notice').textContent = pack.notice; $('about-notice').hidden = false; }
  } else {
    const update = state.updateServer || {};
    if (update.error) {
      $('hero-meta').textContent = '更新服务器连接失败';
      $('download-state').textContent = '无法读取在线版本';
    } else {
      $('hero-meta').textContent = '正在连接更新服务器…';
    }
  }

  const installed = state.installedVersion;
  // 状态栏改成一组带颜色的点，安装到哪一步一眼能看出来
  // 服务器地址不往界面上写：玩家不需要，外人更不需要。
  // 要查地址去日志，那儿是诊断用的。
  $('autojoin-desc').textContent = '跳过多人列表，直连主服务器';
  renderComponents(state.components);

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


  $('about-launcher').textContent = state.launcherVersion || '—';
  $('about-root').textContent = state.root || '—';
  $('about-gamedir').textContent = state.gameDir || '—';
  $('about-ram').textContent = state.totalMemoryMb ? (state.totalMemoryMb + ' MB') : '—';


  renderOptional(state.optional || [], st.enabledOptional || []);
  renderSkin();
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

// packspec 里的 label 写成「名字（说明）」，列表里只留名字。
// 只认全角括号：光影包的 label 是文件名，半角括号可能是文件名本身的一部分。
function optionalName(label) {
  const raw = String(label || '');
  return raw.replace(/（[^）]*）?/g, '').trim() || raw;
}

function renderOptional(items, enabled) {
  optionalItems = items;
  const box = $('optional-list');
  box.innerHTML = '';

  if (!items.length) {
    box.innerHTML = '<div class="list-empty">暂无可选内容</div>';
    return;
  }

  // 按组归拢，一组里的文件通常一起开关。
  //
  // 样式和布局照搬「游戏」页：每项一行，左边只有名字，右边同一个 .switch 开关；
  // 组名用和「已检测到的 Java」一样的小标题。两处看起来就是同一套设置，而不是
  // 一张带原生复选框的文件清单。大小和路径不再逐项列，挤。
  const groups = {};
  items.forEach((it) => { (groups[it.group] = groups[it.group] || []).push(it); });

  Object.keys(groups).forEach((g) => {
    const head = document.createElement('div');
    head.className = 'list-head';
    head.innerHTML = '<span>' + escapeHtml(g) + '</span><span>' + groups[g].length + ' 项 · '
      + humanSize(groups[g].reduce((a, b) => a + (b.size || 0), 0)) + '</span>';
    box.appendChild(head);

    groups[g].forEach((it) => {
      const row = document.createElement('div');
      row.className = 'row';

      const text = document.createElement('div');
      text.className = 'row-text';
      text.innerHTML = '<div class="row-title">' + escapeHtml(optionalName(it.label)) + '</div>';

      const control = document.createElement('div');
      control.className = 'row-control';
      const sw = document.createElement('label');
      sw.className = 'switch';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = typeof it.enabled === 'boolean'
        ? it.enabled
        : (it.paths || [it.path]).every((p) => enabled.indexOf(p) >= 0);
      cb.addEventListener('change', onOptionalToggle);
      cb.dataset.index = String(optionalItems.indexOf(it));
      sw.appendChild(cb);
      sw.appendChild(document.createElement('span'));
      control.appendChild(sw);

      row.appendChild(text);
      row.appendChild(control);
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

/* ───────────────────────── 皮肤 ───────────────────────── */

let skin = null;          // 控制面上的 {model, hash, png}；null = 默认皮肤
let skinDefault = null;   // 没上传时别人看到的样子（Steve），控制面随响应带回来
let skinImage = null;     // { hash, img }，解码过的贴图，render 频繁不能每次重解
let skinBusy = false;

// 正面各部位：贴图里的 [x, y, 宽, 高] → 16×32 人形里的落点 [x, y]。
// 贴图里的"右臂"是角色自己的右手，画在观众左边；纤细模型的手臂窄一列。
// 旧版 64×32 没有左手左腿，游戏里用右边的镜像。
function skinParts(slim, legacy) {
  const a = slim ? 3 : 4;
  const parts = [
    { src: [8, 8, 8, 8], at: [4, 0] },
    { src: [20, 20, 8, 12], at: [4, 8] },
    { src: [44, 20, a, 12], at: [4 - a, 8] },
    { src: [4, 20, 4, 12], at: [4, 20] },
    legacy ? { src: [44, 20, a, 12], at: [12, 8], mirror: true } : { src: [36, 52, a, 12], at: [12, 8] },
    legacy ? { src: [4, 20, 4, 12], at: [8, 20], mirror: true } : { src: [20, 52, 4, 12], at: [8, 20] },
    { src: [40, 8, 8, 8], at: [4, 0] },
  ];
  if (!legacy) {
    parts.push(
      { src: [20, 36, 8, 12], at: [4, 8] },
      { src: [44, 36, a, 12], at: [4 - a, 8] },
      { src: [52, 52, a, 12], at: [12, 8] },
      { src: [4, 36, 4, 12], at: [4, 20] },
      { src: [4, 52, 4, 12], at: [8, 20] });
  }
  return parts;
}

function drawSkin(img, slim) {
  const canvas = $('skin-preview');
  const ctx = canvas.getContext('2d');
  const k = canvas.width / 16;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  if (!img) {
    ctx.fillStyle = '#343140';
    [[4, 0, 8, 8], [4, 8, 8, 12], [0, 8, 4, 12], [12, 8, 4, 12], [4, 20, 4, 12], [8, 20, 4, 12]]
      .forEach(([x, y, w, h]) => ctx.fillRect(x * k, y * k, w * k, h * k));
    return;
  }
  skinParts(slim, img.height === 32).forEach(({ src: [sx, sy, w, h], at: [x, y], mirror }) => {
    if (!mirror) { ctx.drawImage(img, sx, sy, w, h, x * k, y * k, w * k, h * k); return; }
    ctx.save();
    ctx.translate((x + w) * k, y * k);
    ctx.scale(-1, 1);
    ctx.drawImage(img, sx, sy, w, h, 0, 0, w * k, h * k);
    ctx.restore();
  });
}

function decodePng(base64) {
  const img = new Image();
  img.src = 'data:image/png;base64,' + base64;
  return img.decode().then(() => img);
}

// 纤细皮肤的手臂只有 3 像素宽，贴图里 x=54..55、y=20..31 那两列用不到，是全透明的
function looksSlim(img) {
  if (img.height !== 64) return false;
  const c = document.createElement('canvas');
  c.width = 64; c.height = 64;
  const ctx = c.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const data = ctx.getImageData(54, 20, 2, 12).data;
  for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) return false;
  return true;
}

function bytesToBase64(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

let skinOwner = null;     // 当前 skin 属于哪个账号；换号要重新取

function renderSkin() {
  const loggedIn = !!state.account;
  const owner = loggedIn ? String(state.account.muxi_uid || state.account.sub || '') : null;
  if (owner !== skinOwner) {
    skinOwner = owner;
    skin = null;
    const open = document.querySelector('.tab.is-active[data-tab="skin"]');
    if (loggedIn && open) { loadSkin(); return; }
  }
  $('btn-skin-pick').disabled = !loggedIn || skinBusy;
  $('btn-skin-reset').disabled = !loggedIn || skinBusy || !skin;
  // 默认皮肤固定是经典手臂的 Steve，没上传时这个选项没意义
  $('skin-model').disabled = !loggedIn || skinBusy || !skin;
  $('skin-model').value = skin ? skin.model : 'default';
  $('skin-title').textContent = skin ? '自定义皮肤' : '默认皮肤（Steve）';
  $('skin-desc').textContent = !loggedIn ? '登录 muxi 账户后才能设置皮肤'
    : skin ? '聊天头像和关掉 YSM 的玩家会看到这张皮肤'
    : '没上传过皮肤的玩家一律显示 Steve';

  const shown = loggedIn ? (skin || skinDefault) : null;
  const slim = !!shown && shown.model === 'slim';
  if (!shown || !shown.png) { drawSkin(null, slim); return; }
  if (skinImage && skinImage.hash === shown.hash) { drawSkin(skinImage.img, slim); return; }
  const hash = shown.hash;
  decodePng(shown.png)
    .then((img) => {
      skinImage = { hash, img };
      const now = skin || skinDefault;
      if (now && now.hash === hash) drawSkin(img, now.model === 'slim');
    })
    .catch(() => drawSkin(null, slim));
}

function takeSkin(r) {
  skin = r.skin || null;
  if (r.default) skinDefault = r.default;
}

function loadSkin() {
  if (!state.account) { renderSkin(); return; }
  rpc('skinGet')
    .then(takeSkin)
    .catch((e) => toast(e.message, 'error'))
    .finally(renderSkin);
}

function skinCall(method, params, done) {
  skinBusy = true;
  renderSkin();
  rpc(method, params)
    .then((r) => { takeSkin(r); toast(done, 'good'); })
    .catch((e) => toast(e.message, 'error'))
    .finally(() => { skinBusy = false; renderSkin(); });
}

async function onSkinFile() {
  const file = $('skin-file').files[0];
  $('skin-file').value = '';   // 同一个文件再选一次也要能触发
  if (!file) return;
  if (file.size > 64 * 1024) { toast('皮肤文件太大（上限 64 KB）', 'error'); return; }
  const png = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
  let img;
  try { img = await decodePng(png); } catch { toast('不是有效的 PNG 图片', 'error'); return; }
  if (img.width !== 64 || (img.height !== 64 && img.height !== 32)) {
    toast('皮肤尺寸必须是 64×64，这张是 ' + img.width + '×' + img.height, 'error');
    return;
  }
  const model = looksSlim(img) ? 'slim' : 'default';
  $('skin-model').value = model;
  skinCall('skinSave', { png, model }, '皮肤已保存，其他玩家重新进服后看到');
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showLauncherUpdate(u) {
  clientUpdate?.offer(u);
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
  if (clientUpdate?.blocksUse()) { clientUpdate.refreshActivity(); return; }
  try {
    setBusy(true);
    setProgress('准备中', '', -1);
    await rpc('launch', {});
  } catch (e) {
    // 玩家自己点的中止会以"已取消"回来，不是故障，别弹红字也别把日志抽屉拉开
    const aborted = e.message === '已取消';
    const wasRunning = gameRunning;
    setBusy(false);
    if (aborted) {
      $('hero-meta').textContent = wasRunning ? '游戏已中止' : '已取消';
      toast(wasRunning ? '已中止游戏' : '已取消启动', 'good');
      return;
    }
    toast(e.message, 'error', 14000);
    openLog();
  }
}

async function accountLogin() {
  const error = $('account-error');
  try {
    setAccountAuthPending(true);
    const result = await rpc('accountLogin', {});
    error.hidden = true;
    render(result);
    toast('muxi 账户登录成功', 'good');
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  } finally {
    setAccountAuthPending(false);
  }
}

async function accountRegister() {
  const error = $('account-error');
  try {
    setAccountAuthPending(true);
    const result = await rpc('accountRegister', {});
    error.hidden = true;
    render(result);
    toast('muxi 账户注册并登录成功', 'good');
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
  } finally {
    setAccountAuthPending(false);
  }
}

async function installPack() {
  if (busy || packInstallPending) return;
  if (clientUpdate?.blocksUse()) { clientUpdate.refreshActivity(); return; }
  packInstallPending = true;
  try {
    setBusy(true);
    setProgress('准备下载', '', -1);
    const result = await rpc('install', {});
    render(result);
    toast('整合包已经准备就绪', 'good');
  } catch (e) {
    $('download-state').textContent = e.message === '已取消' ? '已取消安装' : '安装未完成';
    toast(e.message, 'error', 14000);
    openLog();
  } finally {
    // The RPC is terminal. A stale busy:true response, error or cancellation
    // must never leave the install/play controls disabled.
    packInstallPending = false;
    state.busy = false;
    setBusy(false);
  }
}

/* ───────────────────────── 绑定 ───────────────────────── */

function bind() {
  clientUpdate = window.createClientUpdateDialog({
    get: $, humanSize,
    currentVersion: () => state.launcherVersion,
    isAppBusy: () => busy || accountAuthPending,
    onStateChange: syncClientActions,
    check: () => transportReady.then(() => invoke('check_client_update')),
    reserve: () => rpc('beginClientUpdate'),
    release: () => rpc('endClientUpdate'),
    install: () => invoke('install_client_update'),
    exit: () => rpc('close'),
    notify: (text) => toast(text, 'good'),
    log: appendLog,
  });
  // 无边框窗口拖动：标题栏空白处按下就交给系统
  $('titlebar').addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.win-btn')) return;
    rpc('beginDrag');
  });

  $('announce-close').onclick = onAnnouncementClosed;
  $('btn-min').onclick = () => rpc('minimize');
  $('btn-close').onclick = () => rpc('close');

  $('btn-play').onclick = play;
  $('btn-account-login').onclick = accountLogin;
  $('btn-account-register').onclick = accountRegister;
  $('btn-account-logout').onclick = () => rpc('accountLogout').then(render).catch((e) => toast(e.message, 'error'));

  $('btn-cancel').onclick = onCancelClicked;

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
    if (clientUpdate?.isOpen()) return;
    if (!$('crash').hidden) $('crash').hidden = true;
    else if (!$('downloads').hidden && !busy) $('downloads').hidden = true;
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
    if (btn.dataset.tab === 'skin') loadSkin();
  });

  // ── 异常退出 / 上传日志 ──
  $('btn-crash-close').onclick = () => { $('crash').hidden = true; };
  $('crash').addEventListener('mousedown', (e) => { if (e.target === $('crash')) $('crash').hidden = true; });
  $('btn-crash-upload').onclick = uploadCrashLogs;
  $('btn-crash-view').onclick = () => {
    if (crashReportId) rpc('openCrashReport', { id: crashReportId }).catch((e) => toast(e.message, 'error'));
  };
  $('btn-crash-folder').onclick = () => rpc('openPath', { which: 'gamelogs' }).catch((e) => toast(e.message, 'error'));
  // 开关只在点上传时带过去（sidecar 顺手记住），不单独存一次
  $('btn-log-upload').onclick = () => { $('settings').hidden = true; showCrash('manual'); };

  // ── 皮肤 ──
  $('btn-skin-pick').onclick = () => $('skin-file').click();
  $('skin-file').addEventListener('change', onSkinFile);
  $('btn-skin-reset').onclick = () => skinCall('skinReset', {}, '已恢复默认皮肤');
  $('skin-model').addEventListener('change', () => {
    if (skin) skinCall('skinSave', { model: $('skin-model').value }, '手臂样式已保存');
  });

  // ── 游戏 ──
  $('memory-range').addEventListener('input', () => {
    $('memory-input').value = $('memory-range').value === '0' ? '' : $('memory-range').value;
    updateMemoryDesc();
    save({ maxMemoryMb: parseInt($('memory-range').value, 10) || 0 });
  });
  $('memory-input').addEventListener('change', () => {
    const max = parseInt($('memory-range').max, 10);
    let v = parseInt($('memory-input').value, 10) || 0;
    // 手填超过上限就当场回正，别让玩家以为自己设上了
    if (v > max) { v = max; $('memory-input').value = String(v); toast('最多只能分配 ' + max + ' MB'); }
    $('memory-range').value = v;
    updateMemoryDesc();
    save({ maxMemoryMb: v });
  });

  $('win-w').addEventListener('change', () => save({ windowWidth: parseInt($('win-w').value, 10) }));
  $('win-h').addEventListener('change', () => save({ windowHeight: parseInt($('win-h').value, 10) }));
  $('gpu').addEventListener('change', () => save({ gpu: $('gpu').value }));
  $('shaderpack').addEventListener('change', () => save({ shaderPack: $('shaderpack').value }));
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
// Client updates do not depend on successfully downloading the game manifest.
clientUpdate.check();
setInterval(() => clientUpdate.check(), 15 * 60 * 1000);
rpc('init')
  .then((s) => {
    render(s);
    if (!s.account && !clientUpdate.isOpen()) $('btn-account-login').focus();
  })
  .catch((e) => {
    $('hero-meta').textContent = '初始化失败';
    toast(e.message, 'error', 20000);
  });
