const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../sidecar/web/app.js'), 'utf8');
const tick = () => new Promise(r => setImmediate(r));
const custom = { skin: { hash: 'fixture-a', model: 'default', png: '' } };

function setup() {
  const elements = new Map(), requests = [];
  const canvas = { clearRect() {}, drawImage() {}, save() {}, restore() {}, translate() {}, scale() {},
    fillRect() {}, getImageData() { return { data: new Uint8Array(96) }; } };
  function element(id) {
    if (!elements.has(id)) elements.set(id, { value: '', files: [], style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} }, addEventListener() {}, appendChild() {}, focus() {},
      querySelectorAll() { return []; }, getContext() { return canvas; }, width: 128 });
    return elements.get(id);
  }
  const updater = { check() {}, isOpen() { return false; } };
  const context = { console, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
    Uint8Array, btoa: s => Buffer.from(s, 'binary').toString('base64'),
    Image: class { width = 64; height = 64; decode() { return Promise.resolve(); } },
    document: { getElementById: element, querySelectorAll: () => [], querySelector: () => null,
      addEventListener() {}, createElement: () => element('generated') },
    window: { createClientUpdateDialog: () => updater, __TAURI__: { event: { listen: async () => {} },
      core: { invoke: async () => {} } } },
    fakeRpc(method, params) {
      return new Promise((resolve, reject) => requests.push({ method, params, resolve, reject }));
    },
  };
  vm.runInNewContext(source + `
    rpc = fakeRpc;
    this.api = { loadSkin, skinCall, onSkinFile,
      setAccount(uid) { state.account = uid ? {muxi_uid: uid} : null; renderSkin(); },
      shown() { return skin; }
    };`, context);
  context.api.setAccount('90001');
  return { api: context.api, element, requests };
}

test('a pre-save default GET cannot overwrite successful saved skin', async () => {
  const s = setup(); s.api.loadSkin(); s.api.skinCall('skinSave', { png: 'fixture', model: 'default' }, 'saved');
  s.requests.find(r => r.method === 'skinSave').resolve(custom); await tick();
  assert.equal(s.api.shown().hash, 'fixture-a');
  s.requests.find(r => r.method === 'skinGet').resolve({ skin: null }); await tick();
  assert.equal(s.api.shown()?.hash, 'fixture-a');
});

test('an old account skin GET cannot appear under a new account', async () => {
  const s = setup(); s.api.loadSkin(); s.api.setAccount('90002');
  s.requests[0].resolve(custom); await tick();
  assert.equal(s.api.shown(), null);
});

test('a file selected before an account switch is never saved under the new UID', async () => {
  const s = setup(); let finish;
  s.element('skin-file').files = [{ size: 4, arrayBuffer: () => new Promise(r => { finish = r; }) }];
  const selection = s.api.onSkinFile(); s.api.setAccount('90002');
  finish(new Uint8Array([1, 2, 3, 4]).buffer); await selection; await tick();
  assert.equal(s.requests.filter(r => r.method === 'skinSave').length, 0);
});

test('save failure is reported and never previews an unpersisted new image', async () => {
  const s = setup(); s.api.loadSkin(); s.requests[0].resolve(custom); await tick();
  s.api.skinCall('skinSave', { png: 'new-fixture', model: 'slim' }, 'saved');
  s.requests[1].reject(new Error('fixture HTTP 500')); await tick();
  assert.equal(s.api.shown().hash, 'fixture-a');
  assert.equal(s.element('toast-text').textContent, 'fixture HTTP 500');
});

test('successful selection sends PNG/model and only accepts persisted server result', async () => {
  const s = setup(); s.element('skin-file').files = [{ size: 4,
    arrayBuffer: async () => new Uint8Array([1, 2, 3, 4]).buffer }];
  await s.api.onSkinFile(); await tick();
  const req = s.requests.find(r => r.method === 'skinSave');
  assert.equal(req.params.png, 'AQIDBA=='); assert.equal(req.params.model, 'slim');
  assert.equal(s.api.shown(), null);
  req.resolve(custom); await tick(); assert.equal(s.api.shown().hash, 'fixture-a');
});

test('an old save response is discarded after switching accounts', async () => {
  const s = setup(); s.api.skinCall('skinSave', { png: 'fixture', model: 'default' }, 'saved');
  s.api.setAccount('90002'); s.requests[0].resolve(custom); await tick();
  assert.equal(s.api.shown(), null);
  assert.equal(s.element('toast-text').textContent, undefined);
  const currentRead = s.requests.find(r => r.method === 'skinGet');
  assert.ok(currentRead); currentRead.resolve({ skin: { hash: 'fixture-b', model: 'slim', png: '' } });
  await tick(); assert.equal(s.api.shown().hash, 'fixture-b');
});

test('new reads and duplicate writes cannot race a pending save', async () => {
  const s = setup(); s.api.skinCall('skinSave', { png: 'fixture', model: 'default' }, 'saved');
  s.api.loadSkin(); s.api.skinCall('skinReset', {}, 'reset');
  assert.equal(s.requests.length, 1);
  s.requests[0].resolve(custom); await tick(); assert.equal(s.api.shown().hash, 'fixture-a');
});

test('file read failure is visible and sends no save', async () => {
  const s = setup(); s.element('skin-file').files = [{ size: 4, arrayBuffer: async () => { throw new Error('read failed'); } }];
  await s.api.onSkinFile(); await tick();
  assert.equal(s.requests.length, 0); assert.ok(s.element('toast-text').textContent);
  assert.match(s.element('toast').className, /error/);
});
