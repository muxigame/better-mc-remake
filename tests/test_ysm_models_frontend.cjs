'use strict';
// Local browser-logic tests only: these do not substitute for native MCEF acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.join(__dirname, '../server/web/ysm-models.js');
const {fileError, canUse, canDownload, sha256File, profileUID, createUploadTask, DEFAULT_LIMITS} = require(frontend);
const tick = () => new Promise(resolve => setImmediate(resolve));
class XHR {
  constructor() { this.upload = {}; this.headers = {}; this.status = 200; this.responseText = '{}'; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(key, value) { this.headers[key] = value; }
  send(file) { this.file = file; }
  abort() { this.onabort(); }
  reply(status, body) { this.status = status; this.responseText = JSON.stringify(body); this.onload(); }
}
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.value = ''; this.hidden = false; this.disabled = false; this.checked = false; this.textContent = ''; this.open = false; this.className = ''; this.classList = {toggle() {}, add() {}, remove() {}}; }
  append(...elements) { elements.forEach(element => { element.parent = this; this.children.push(element); }); }
  replaceChildren(...elements) { this.children = []; this.append(...elements); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  removeEventListener(name, handler) { this.listeners[name] = (this.listeners[name] || []).filter(value => value !== handler); }
  setAttribute(name, value) { this.attributes[name] = value; }
  contains(element) { return this === element || this.children.includes(element); }
  async fire(name, values = {}) { for (const handler of [...(this.listeners[name] || [])]) await handler({target: this, preventDefault() {}, ...values}); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { return this.fire('click'); }
}
function descendants(element, tag) { return element.children.flatMap(child => [child, ...descendants(child, tag)]).filter(child => !tag || child.tagName === tag); }
async function main() {
  const file = {name: '测试 & 模型.ysm', size: 1024};
  assert.equal(profileUID({user: {uid: 9007199254740993}, player: {loginName: '9007199254740993'}}), '9007199254740993');
  assert.equal(profileUID({user: {uid: '9007199254740993'}}), '9007199254740993');
  assert.equal(profileUID({user: {uid: 10000}}), '10000');
  assert.throws(() => profileUID({user: {uid: 9007199254740993}}), /精确/);
  assert.throws(() => profileUID({user: {uid: true}}), /精确/);
  assert.equal(fileError(file), '');
  for (const name of ['../bad.ysm', 'x\\bad.ysm', 'x.ysm.exe', 'x\n.ysm']) assert.ok(fileError({name, size: 1}));
  assert.ok(fileError({name: 'x.ysm', size: 0}));
  assert.ok(fileError({name: 'x.ysm', size: DEFAULT_LIMITS.maxFileBytes + 1}));
  const pending = {id: 'a', validationStatus: 'pending', syncStatus: 'ready', canSelect: true, canDownload: true};
  assert.equal(canUse(pending), false); assert.equal(canDownload(pending), false);
  const ready = {...pending, validationStatus: 'verified'};
  assert.equal(canUse(ready), true); assert.equal(canDownload(ready), true);
  assert.equal(canUse({...ready, syncStatus: 'pending'}), false);
  assert.equal(canUse({...ready, deletedAt: '2026-10-13T01:00:00Z'}), false);
  assert.equal(canDownload({...ready, deletedAt: '2026-10-13T01:00:00Z'}), false);
  const requests = [], changes = [];
  const task = createUploadTask(file, 'unexpected', {key: 'fixed-key', expectedUID: '10000', createXHR() { const xhr = new XHR(); requests.push(xhr); return xhr; }, onChange(value) { changes.push(value.state); }});
  assert.equal(task.visibility, 'private');
  let result = task.start();
  assert.ok(requests[0].url.includes('filename=' + encodeURIComponent(file.name)));
  assert.ok(requests[0].url.endsWith('&visibility=private'));
  assert.equal(requests[0].headers['Idempotency-Key'], 'fixed-key');
  assert.equal(requests[0].headers['X-Muxi-Expected-UID'], '10000');
  assert.equal(requests[0].withCredentials, true); assert.ok(!('Authorization' in requests[0].headers));
  requests[0].upload.onprogress({lengthComputable: true, loaded: 1, total: 1}); assert.equal(task.progress, 99);
  task.setVisibility('public'); assert.equal(task.visibility, 'private');
  requests[0].onerror(); await result; assert.equal(task.state, 'error');
  result = task.start(); assert.equal(requests[1].url, requests[0].url); assert.equal(requests[1].headers['Idempotency-Key'], 'fixed-key');
  task.cancel(); await result; assert.equal(task.state, 'cancelled');
  result = task.start(); requests[2].reply(201, {model: pending}); await result;
  assert.equal(task.state, 'complete'); assert.equal(task.progress, 100); assert.deepEqual(task.model, pending);
  await task.start(); assert.equal(requests.length, 3);
  let unauthorized = 0;
  const denied = createUploadTask(file, 'private', {key: 'denied', createXHR() { const xhr = new XHR(); requests.push(xhr); return xhr; }, onUnauthorized() { unauthorized++; }});
  result = denied.start(); requests[3].reply(401, {detail: '请登录'}); await result;
  assert.equal(unauthorized, 1); assert.equal(denied.error, '请登录');

  if (!globalThis.crypto) globalThis.crypto = require('node:crypto').webcrypto;
  assert.equal(await sha256File({size: 3, arrayBuffer: async () => new Uint8Array([97, 98, 99]).buffer}),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  await assert.rejects(sha256File({size: DEFAULT_LIMITS.maxFileBytes + 1, arrayBuffer: async () => { throw Error('must not allocate'); }}), /校验限制/);
  await assert.rejects(sha256File({size: 3, arrayBuffer: async () => new ArrayBuffer(4)}), /大小不一致/);
  const digest = 'a'.repeat(64), dedupRequests = [];
  for (const visibility of ['private', 'public']) {
    const dedup = createUploadTask(file, visibility, {key: 'dedup-' + visibility, expectedUID: '10000', hashFile: async () => digest,
      createXHR() { throw Error('Public hit must never upload the file'); },
      fetch: async (url, options) => {
        dedupRequests.push({url, options});
        return {ok: true, status: 200, json: async () => ({model: url.endsWith('/references') ? {...ready, visibility} : {id: 'p_public-source'}})};
      }});
    await dedup.start(); assert.equal(dedup.state, 'complete'); assert.equal(dedup.reusedPublic, true);
    const request = dedupRequests.at(-1);
    assert.deepEqual(JSON.parse(request.options.body), {publicId: 'p_public-source', filename: file.name, visibility});
    assert.equal(request.options.headers['Idempotency-Key'], 'dedup-' + visibility);
    assert.equal(request.options.headers['X-Muxi-Expected-UID'], '10000');
    assert.equal(request.options.credentials, 'same-origin');
  }
  let fallbackXHR, lookupCalls = 0;
  const notPublic = createUploadTask(file, 'private', {key: 'normal-upload', hashFile: async () => digest,
    createXHR() { fallbackXHR = new XHR(); return fallbackXHR; },
    fetch: async (url) => { lookupCalls++; assert.equal(url, '/api/v1/ysm/models/public/by-sha256/' + digest); return {ok: false, status: 404, json: async () => ({detail: 'not found'})}; }});
  result = notPublic.start(); await tick(); fallbackXHR.onerror(); await result;
  result = notPublic.start(); fallbackXHR.reply(201, {model: pending}); await result;
  assert.equal(lookupCalls, 1); // An unknown raw-upload outcome keeps the original route, too.
  assert.equal(fallbackXHR.headers['Idempotency-Key'], 'normal-upload');
  let resolveHash, cancelledFetches = 0;
  const cancelling = createUploadTask(file, 'private', {key: 'cancel-hash', hashFile: () => new Promise(resolve => { resolveHash = resolve; }),
    fetch: async () => { cancelledFetches++; }, createXHR() { throw Error('Cancelled hash must not upload'); }});
  result = cancelling.start(); assert.equal(cancelling.state, 'hashing'); cancelling.cancel(); await result;
  resolveHash(digest); await tick(); assert.equal(cancelledFetches, 0); assert.equal(cancelling.state, 'cancelled');
  const hashResolvers = [], lateRequests = [];
  const cancelledRetry = createUploadTask(file, 'private', {key: 'late-retry', hashFile: () => new Promise(resolve => hashResolvers.push(resolve)),
    createXHR() { throw Error('A public hit must not upload'); }, fetch: async (url, options) => {
      lateRequests.push({url, options});
      return {ok: true, status: 200, json: async () => ({model: url.endsWith('/references') ? ready : {id: 'p_retry-source'}})};
    }});
  result = cancelledRetry.start(); cancelledRetry.cancel(); await result;
  const retryResult = cancelledRetry.start(); hashResolvers[1](digest); await retryResult;
  hashResolvers[0]('b'.repeat(64)); await tick();
  assert.equal(cancelledRetry.state, 'complete'); assert.equal(lateRequests.length, 2);
  assert.ok(lateRequests[0].url.endsWith(digest)); assert.equal(lateRequests[1].options.headers['Idempotency-Key'], 'late-retry');
  let currentAccount = true, lateHash;
  const switched = createUploadTask(file, 'private', {key: 'switch-hash', isCurrent: () => currentAccount,
    hashFile: () => new Promise(resolve => { lateHash = resolve; }), fetch: async () => { throw Error('New account must not receive old reference'); }});
  result = switched.start(); currentAccount = false; lateHash(digest); await result; assert.equal(switched.state, 'cancelled');
  let networkUploads = 0;
  const networkFailure = createUploadTask(file, 'private', {key: 'network-check', hashFile: async () => digest,
    fetch: async () => { throw Error('offline'); }, createXHR() { networkUploads++; }});
  await networkFailure.start(); assert.equal(networkFailure.state, 'error'); assert.equal(networkUploads, 0);
  const referenceCalls = [];
  let refAttempts = 0;
  const changedSource = createUploadTask(file, 'private', {key: 'reference-key', hashFile: async () => digest,
    createXHR() { throw Error('Private source cannot silently trigger raw upload'); },
    fetch: async (url, options) => {
      referenceCalls.push({url, options});
      if (!url.endsWith('/references')) return {ok: true, status: 200, json: async () => ({model: {id: 'p_changed'}})};
      refAttempts++;
      return {ok: false, status: 404, json: async () => ({detail: '公开来源已撤销'})};
    }});
  await changedSource.start(); assert.equal(changedSource.state, 'error'); assert.equal(changedSource.error, '公开来源已撤销');
  await changedSource.start(); assert.equal(refAttempts, 2); assert.equal(referenceCalls.length, 3);
  assert.equal(referenceCalls[1].options.body, referenceCalls[2].options.body);
  assert.equal(referenceCalls[2].options.headers['Idempotency-Key'], 'reference-key');
  let accountChanges = 0;
  const wrongAccount = createUploadTask(file, 'private', {key: 'account-reference', expectedUID: '10000', hashFile: async () => digest,
    onAccountChanged() { accountChanges++; }, createXHR() { throw Error('Changed account must not receive file'); },
    fetch: async (url, options) => {
      assert.equal(options.headers['X-Muxi-Expected-UID'], '10000');
      return url.endsWith('/references') ? {ok: false, status: 409, json: async () => ({detail: 'account_changed'})}
        : {ok: true, status: 200, json: async () => ({model: {id: 'p_account'}})};
    }});
  await wrongAccount.start(); assert.equal(wrongAccount.state, 'error'); assert.equal(accountChanges, 1);

  const elements = new Map(), allRequests = [], uploadXHRs = [];
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  get('ysm-upload-visibility').value = 'private';
  let denyMine = false, profile = {user: {uid: '10000'}};
  let mine = [{...pending, id: 'm1', name: '<img src=x onerror=alert(1)>.ysm', size: 12, visibility: 'public', deletedAt: null},
    {...ready, id: 'm2', name: 'Ready.ysm', size: 15, visibility: 'private', deletedAt: null},
    {...ready, id: 'm3', name: 'Deleted.ysm', size: 15, visibility: 'private', deletedAt: '2026-10-13T01:00:00Z'}];
  const context = {document: {getElementById: get, createElement: tag => new Element(tag), body: new Element('body')},
    window: {confirm: () => true}, crypto: {randomUUID: () => 'test-uuid'}, Uint8Array, console, setTimeout,
    XMLHttpRequest: class extends XHR { constructor() { super(); uploadXHRs.push(this); } },
    fetch: async (url, options) => {
      allRequests.push({url, options});
      if (url === '/api/v1/player/profile') return {ok: true, json: async () => profile};
      if (url === '/api/v1/ysm/models/public') return {ok: true, json: async () => ({models: [{...ready, id: 'p_opaque', name: 'Public.ysm', size: 10, visibility: 'public'}]})};
      if (denyMine && url === '/api/v1/player/ysm/models') return {ok: false, status: 401, json: async () => ({detail: '账户已失效'})};
      if (url.endsWith('/visibility')) { mine = mine.map(model => model.id === 'm2' ? {...model, visibility: JSON.parse(options.body).visibility} : model); return {ok: true, json: async () => ({model: mine[1]})}; }
      if (url.endsWith('/restore')) { mine = mine.map(model => model.id === 'm3' ? {...model, deletedAt: null} : model); return {ok: true, json: async () => ({model: mine[2]})}; }
      return {ok: true, json: async () => ({models: mine, selectedModelId: null, limits: DEFAULT_LIMITS})};
    }};
  vm.runInNewContext(fs.readFileSync(frontend, 'utf8'), context, {filename: frontend});
  await tick(); await tick();
  assert.equal(get('ysm-private-content').hidden, false);
  assert.equal(get('ysm-my-list').children.length, 2);
  const pendingRow = get('ysm-my-list').children[0];
  assert.equal(descendants(pendingRow, 'h3')[0].textContent, mine[0].name);
  assert.ok(descendants(pendingRow, 'button').filter(b => ['下载', '选用'].includes(b.textContent)).every(b => b.disabled));
  assert.ok(descendants(pendingRow).every(e => e.tagName !== 'img'));
  assert.equal(get('ysm-public-list').children.length, 1);
  get('ysm-show-deleted').checked = true; await get('ysm-show-deleted').fire('change');
  assert.equal(get('ysm-my-list').children.length, 3);
  await descendants(get('ysm-my-list').children[2], 'button').find(b => b.textContent === '恢复为私有').click();
  assert.equal(mine[2].deletedAt, null);
  assert.equal(allRequests.find(request => request.url.endsWith('/restore')).options.headers['X-Muxi-Expected-UID'], '10000');
  const input = get('ysm-files'); input.files = [file, {name: 'bad.exe', size: 4}, {name: 'Second.ysm', size: 5}]; await input.fire('change');
  assert.equal(get('ysm-upload-list').children.length, 3);
  assert.equal(descendants(get('ysm-upload-list').children[0], 'select')[0].value, 'private');
  assert.ok(descendants(get('ysm-upload-list').children[1], 'button').find(b => b.textContent === '重试').hidden);
  const queuedRun = get('ysm-upload-start').fire('click'); await tick();
  assert.equal(uploadXHRs.length, 1);
  assert.equal(uploadXHRs[0].headers['X-Muxi-Expected-UID'], '10000');
  const cancelLast = descendants(get('ysm-upload-list').children[2], 'button').find(b => b.textContent === '取消'); await cancelLast.click();
  uploadXHRs[0].reply(201, {model: pending}); await queuedRun; await tick();
  assert.equal(uploadXHRs.length, 1); // Cancelling a waiting file never starts it later.
  await get('ysm-upload-clear').click(); assert.equal(get('ysm-upload-list').children.length, 0);
  const privateRow = get('ysm-my-list').children.find(row => descendants(row, 'h3')[0].textContent === 'Ready.ysm');
  const select = descendants(privateRow, 'select')[0]; select.value = 'public';
  const change = select.fire('change'); await tick(); assert.equal(get('ysm-confirm').open, true);
  assert.ok(!allRequests.some(request => request.url.endsWith('/visibility'))); // Explicit confirmation required.
  await get('ysm-confirm-ok').click(); await change; assert.equal(mine[1].visibility, 'public');
  assert.equal(allRequests.find(request => request.url.endsWith('/visibility')).options.headers['X-Muxi-Expected-UID'], '10000');
  const publicOwnRow = get('ysm-my-list').children.find(row => descendants(row, 'h3')[0].textContent === 'Ready.ysm');
  const makePrivate = descendants(publicOwnRow, 'select')[0]; makePrivate.value = 'private';
  const privateChange = makePrivate.fire('change'); await tick();
  assert.ok(get('ysm-confirm-text').textContent.includes('已下载的副本无法召回'));
  await get('ysm-confirm-cancel').click(); await privateChange; assert.equal(mine[1].visibility, 'public');
  input.files = [file]; await input.fire('change');
  const switchedQueue = get('ysm-upload-start').click(); await tick();
  uploadXHRs.at(-1).reply(409, {detail: 'account_changed'}); await switchedQueue;
  assert.equal(get('ysm-private-content').hidden, true); assert.equal(get('ysm-upload-list').children.length, 0);
  assert.ok(get('ysm-message').textContent.includes('账户已切换'));
  denyMine = true; await get('ysm-refresh').click(); await tick();
  assert.equal(get('ysm-private-content').hidden, true); assert.equal(get('ysm-login').hidden, false);
  assert.equal(get('ysm-my-list').children.length, 0);
  assert.ok(descendants(get('ysm-public-list'), 'button').every(button => button.disabled));
  // A fresh page must pin the exact server loginName, never String(an already rounded JSON Number).
  denyMine = false; profile = {user: {uid: 9007199254740993}, player: {loginName: '9007199254740993'}};
  elements.clear(); get('ysm-upload-visibility').value = 'private'; get('ysm-private-content').hidden = true;
  const largeStart = allRequests.length;
  vm.runInNewContext(fs.readFileSync(frontend, 'utf8'), context, {filename: frontend}); await tick(); await tick();
  assert.equal(get('ysm-private-content').hidden, false);
  assert.equal(allRequests.slice(largeStart).find(request => request.url === '/api/v1/player/ysm/models').options.headers['X-Muxi-Expected-UID'], '9007199254740993');
  get('ysm-files').files = [file]; await get('ysm-files').fire('change');
  const largeUpload = get('ysm-upload-start').click(); await tick();
  assert.equal(uploadXHRs.at(-1).headers['X-Muxi-Expected-UID'], '9007199254740993');
  uploadXHRs.at(-1).reply(201, {model: pending}); await largeUpload;
  profile = {user: {uid: 9007199254740993}}; await get('ysm-refresh').click(); await tick();
  assert.equal(get('ysm-private-content').hidden, true); assert.equal(get('ysm-my-list').children.length, 0);
  assert.equal(get('ysm-upload-list').children.length, 0);
  profile = {user: {uid: 9007199254740993}}; elements.clear(); get('ysm-upload-visibility').value = 'private'; get('ysm-private-content').hidden = true;
  const unsafeStart = allRequests.length;
  vm.runInNewContext(fs.readFileSync(frontend, 'utf8'), context, {filename: frontend}); await tick(); await tick();
  assert.equal(get('ysm-private-content').hidden, true);
  assert.ok(!allRequests.slice(unsafeStart).some(request => request.url === '/api/v1/player/ysm/models'));
  assert.ok(get('ysm-message').textContent.includes('精确'));
  assert.ok(allRequests.every(request => request.options.credentials === 'same-origin' && !request.options.headers.Authorization));
  assert.ok(!fs.readFileSync(frontend, 'utf8').includes('localStorage'));
  assert.ok(!fs.readFileSync(frontend, 'utf8').includes('innerHTML'));
  console.log('PASS: YSM bounded SHA256, public-reference dedup without file upload, private/unknown lookup fallback, network failure, hash/account cancellation, revoked-source retry, upload idempotency, literal DOM, restore and explicit public confirmation.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
