const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../sidecar/web/client-update.js'), 'utf8');
const release = { version: '1.2.0', currentVersion: '1.1.5', notes: '修复问题', size: 100, mandatory: false };
function setup(overrides = {}) {
  const elements = new Map();
  let closed = 0;
  let busy = false;
  const document = { activeElement: null };
  function get(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        open: false, hidden: false, disabled: false, textContent: '', style: {}, attrs: {}, listeners: {}, isConnected: true,
        classList: { toggle(k, b) { if (b) classes.add(k); else classes.delete(k); } },
        addEventListener(k, cb) { this.listeners[k] = cb; },
        setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
        showModal() { this.open = true; }, close() { this.open = false; }, focus() { document.activeElement = this; },
      });
    }
    return elements.get(id);
  }
  const calls = [];
  const deps = {
    get, currentVersion: () => '1.1.5', humanSize: x => `${x} B`,
    isAppBusy: () => busy, onStateChange() {}, check: async () => release,
    reserve: async () => { calls.push('reserve'); }, release: async () => { calls.push('release'); },
    install: async () => { calls.push('install'); }, exit: () => { closed++; }, notify() {}, log() {}, ...overrides,
  };
  const context = { window: {}, document, Date, console };
  vm.runInNewContext(source, context);
  const api = context.window.createClientUpdateDialog(deps);
  return { api, get, calls, setBusy(v) { busy = v; }, closed: () => closed };
}

test('detecting an update opens a modal, not the settings panel', () => {
  const {api, get} = setup(); api.offer(release);
  assert.equal(get('client-update-dialog').open, true);
  assert.equal(get('cu-version').textContent, '1.2.0');
  assert.equal(get('cu-current').textContent, '1.1.5');
});
test('postpone optional update: repeated state does not reopen it', async () => {
  const {api, get} = setup(); api.offer(release); get('cu-later').onclick();
  api.offer(release); api.refreshActivity();
  assert.equal(get('client-update-dialog').open, false);
  assert.equal(api.blocksUse(), false);
  await api.check(true);
  assert.equal(get('client-update-dialog').open, true);
});
test('required update cannot be dismissed via Escape, close or postpone', () => {
  const {api, get, closed} = setup(); api.offer({...release, mandatory: true});
  let prevented = false;
  get('client-update-dialog').listeners.cancel({preventDefault() { prevented = true; }});
  get('cu-close').onclick();
  assert.equal(prevented, true);
  assert.equal(get('client-update-dialog').open, true);
  assert.equal(get('cu-close').hidden, true);
  assert.equal(api.blocksUse(), true);
  assert.equal(get('cu-later').textContent, '退出客户端');
  get('cu-later').onclick(); assert.equal(closed(), 1);
});
test('one-version policy escalation reopens a postponed optional release', () => {
  const {api, get} = setup(); api.offer(release); get('cu-later').onclick();
  api.offer({...release, mandatory: true});
  assert.equal(get('client-update-dialog').open, true);
});
test('do not interrupt a running game; show dialog once activity ends', () => {
  const s = setup(); s.setBusy(true); s.api.offer(release);
  assert.equal(s.get('client-update-dialog').open, false);
  s.setBusy(false); s.api.refreshActivity();
  assert.equal(s.get('client-update-dialog').open, true);
});
test('download progress, verification and installer handoff stay in the modal', async () => {
  let finish;
  const s = setup({install: () => new Promise(resolve => { finish = resolve; })});
  s.api.offer(release);
  const job = s.get('cu-update').onclick(); await new Promise(setImmediate);
  s.api.progress({phase: 'started', version: '1.2.0'});
  s.api.progress({phase: 'downloading', downloaded: 45, total: 100});
  assert.equal(s.get('cu-percent').textContent, '45%');
  assert.equal(s.get('cu-meter').attrs['aria-valuenow'], '45');
  assert.equal(s.get('cu-progress').hidden, false);
  assert.equal(s.get('cu-close').hidden, true);
  s.api.offer(release); s.api.refreshActivity();
  assert.equal(s.get('cu-percent').textContent, '45%');
  s.api.progress({phase: 'verifying'});
  assert.equal(s.get('cu-phase').textContent, '校验签名');
  s.api.progress({phase: 'installing'});
  assert.equal(s.get('cu-phase').textContent, '启动安装器');
  finish(); await job;
});
test('download failure permits retry; optional update can still be postponed', async () => {
  let installs = 0;
  const s = setup({install: async () => { installs++; throw new Error('下载失败'); }});
  s.api.offer(release); await s.get('cu-update').onclick();
  assert.equal(s.get('cu-error').hidden, false);
  assert.equal(s.get('cu-error').textContent, '下载失败');
  assert.equal(s.get('cu-update').disabled, false);
  assert.equal(s.api.blocksUse(), false);
  await s.get('cu-update').onclick(); assert.equal(installs, 2);
  s.get('cu-later').onclick(); assert.equal(s.get('client-update-dialog').open, false);
});
test('required update remains blocking after network/signature failure', async () => {
  const s = setup({install: async () => { throw new Error('签名校验失败'); }});
  s.api.offer({...release, mandatory: true}); await s.get('cu-update').onclick();
  assert.equal(s.api.blocksUse(), true);
  assert.equal(s.get('cu-update').disabled, false);
  assert.equal(s.get('cu-later').textContent, '退出客户端');
});
test('double clicks cannot start two installers', async () => {
  let finish, count = 0;
  const s = setup({install: () => { count++; return new Promise(r => { finish = r; }); }});
  s.api.offer(release);
  const first = s.get('cu-update').onclick();
  await s.get('cu-update').onclick(); await Promise.resolve();
  assert.equal(count, 1); finish(); await first;
});
test('native check works independently of game manifest and deduplicates startup', async () => {
  const s = setup(); await s.api.check(); s.api.offer(release);
  s.get('cu-later').onclick(); await s.api.check();
  assert.equal(s.get('client-update-dialog').open, false);
});
test('cached optional state cannot undo a fresh native required policy', async () => {
  const s = setup({check: async () => ({...release, mandatory: true})});
  await s.api.check(); s.api.offer(release);
  assert.equal(s.api.blocksUse(), true);
});
test('fresh game preflight can escalate an earlier optional native check', async () => {
  const s = setup(); await s.api.check(); s.get('cu-later').onclick();
  s.api.offer({...release, mandatory: true});
  assert.equal(s.api.blocksUse(), true);
  assert.equal(s.get('client-update-dialog').open, true);
});
test('current version shows no update popup', async () => {
  const s = setup({check: async () => null}); await s.api.check();
  assert.equal(s.get('client-update-dialog').open, false);
  assert.equal(s.get('client-update-status').textContent, '已是最新版本');
});
test('privacy prose removed from sign-in area', () => {
  const html = fs.readFileSync(path.join(__dirname, '../sidecar/web/index.html'), 'utf8');
  assert.equal(html.includes('account-oauth-note'), false);
  assert.equal(html.includes('启动器不会接收你的密码'), false);
});
