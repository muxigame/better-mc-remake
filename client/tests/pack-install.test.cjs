const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the real app.js RPC/event ordering, rather than a duplicate model.
const source = fs.readFileSync(path.join(__dirname, '../sidecar/web/app.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const ready = () => ({ busy: false, launcherVersion: '1.1.7',
  account: { username: 'Test', nickname: '测试' }, settings: {}, optional: [],
  installedVersion: '1.3.1', pack: { name: 'Test Pack', version: '1.3.1', javaMajor: 21 } });

async function setup() {
  const elements = new Map(), requests = [];
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, { hidden: false, disabled: false, value: '', textContent: '', style: {},
        scrollHeight: 0, scrollTop: 0, clientHeight: 0, childElementCount: 0, dataset: {},
        classList: { add(x) { classes.add(x); }, remove(x) { classes.delete(x); },
          toggle(x,b) { b ? classes.add(x) : classes.delete(x); } },
        addEventListener() {}, appendChild() {}, focus() {}, querySelectorAll: () => [],
      });
    }
    return elements.get(id);
  }
  const updater = { blocksUse: () => false, refreshActivity() {}, check: async () => {},
    isOpen: () => false, offer() {}, progress() {} };
  const context = { console, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
    document: { getElementById: element, querySelectorAll: () => [], addEventListener() {},
      createElement: () => element(`generated-${elements.size}`) },
    window: { createClientUpdateDialog: () => updater, __TAURI__: {
      event: { listen: async () => {} }, core: { invoke: async (name, args) => {
        if (name === 'send_message') requests.push(JSON.parse(args.json));
      } },
    } },
  };
  vm.runInNewContext(source + '\nthis.api = { receive, installPack, render };', context);
  await tick();
  const init = requests.find(r => r.method === 'init');
  context.api.receive({ id: init.id, ok: true, result: ready() });
  await tick();
  return { api: context.api, element, requests, updater };
}

for (const staleBusy of [false, true]) {
  test(`completed install unlocks play/repair even with RPC busy=${staleBusy}`, async () => {
    const s = await setup();
    const pending = s.api.installPack(); await tick();
    const req = s.requests.find(r => r.method === 'install');
    assert.equal(s.element('btn-install-pack').disabled, true);
    s.api.receive({ event: 'busy', payload: { busy: false } });
    // Do not let a delayed terminal event allow a second task before the RPC.
    assert.equal(s.element('btn-install-pack').disabled, true);
    s.api.receive({ id: req.id, ok: true, result: { ...ready(), busy: staleBusy } });
    await pending;
    assert.equal(s.element('btn-install-pack').disabled, false);
    assert.equal(s.element('btn-play').disabled, false);
    assert.equal(s.element('btn-download-cancel').hidden, true);
    assert.equal(s.element('working').hidden, true);
    assert.equal(s.element('btn-install-pack').textContent, '检查并修复');
  });
}

for (const error of ['已取消', 'NeoForge 安装失败']) {
  test(`failed/cancelled install releases controls: ${error}`, async () => {
    const s = await setup(); const pending = s.api.installPack(); await tick();
    const req = s.requests.find(r => r.method === 'install');
    s.api.receive({ id: req.id, ok: false, error }); await pending;
    assert.equal(s.element('btn-download-cancel').hidden, true);
    assert.equal(s.element('btn-install-pack').disabled, false);
    assert.equal(s.element('btn-play').disabled, false);
    assert.equal(s.element('download-state').textContent, error === '已取消' ? '已取消安装' : '安装未完成');
  });
}

test('runtime installation after download still shows the active stage, no false success', async () => {
  const s = await setup(); const pending = s.api.installPack(); await tick();
  const req = s.requests.find(r => r.method === 'install');
  s.api.receive({ event: 'status', payload: { phase: '下载', fraction: 1 } });
  s.api.receive({ event: 'status', payload: { phase: '安装 NeoForge', fraction: -1 } });
  assert.equal(s.element('download-state').textContent, '安装 NeoForge');
  assert.equal(s.element('btn-install-pack').disabled, true);
  assert.equal(s.element('btn-download-cancel').hidden, false);
  s.api.receive({ id: req.id, ok: true, result: ready() }); await pending;
  assert.equal(s.element('download-state').textContent, '已安装最新版本');
});

test('double-click cannot create duplicate installations', async () => {
  const s = await setup(); const pending = s.api.installPack();
  await s.api.installPack(); await tick();
  const installs = s.requests.filter(r => r.method === 'install');
  assert.equal(installs.length, 1);
  s.api.receive({ id: installs[0].id, ok: true, result: ready() }); await pending;
});

test('required client update remains blocking after pack completion', async () => {
  const s = await setup(); const pending = s.api.installPack(); await tick();
  const req = s.requests.find(r => r.method === 'install');
  s.updater.blocksUse = () => true;
  s.api.receive({ id: req.id, ok: true, result: ready() }); await pending;
  assert.equal(s.element('btn-play').disabled, true);
  assert.equal(s.element('btn-install-pack').disabled, true);
  assert.equal(s.element('btn-download-cancel').hidden, true);
});

test('download page does not show infrastructure explanation', () => {
  const html = fs.readFileSync(path.join(__dirname, '../sidecar/web/index.html'), 'utf8');
  assert.equal(html.includes('Minecraft 本体与运行库来自官方 CDN'), false);
});

test('signed-in identity and play button use separate rows', () => {
  const css = fs.readFileSync(path.join(__dirname, '../sidecar/web/app.css'), 'utf8');
  assert.match(css, /\.account-ready\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column/s);
  assert.match(css, /\.account-ready\s+\.mc-btn\s*\{[^}]*width:\s*100%/s);
});
