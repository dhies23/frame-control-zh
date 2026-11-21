// Desktop "Frame Control in VR" panel controls use synthetic replies only; no backend or Frame action.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../../ui/index.html', import.meta.url), 'utf8');
const start = html.indexOf('let headsetInfo = null;');
const end = html.indexOf("window.addEventListener('message'", start);
assert.ok(start > 0 && end > start, 'headset panel slice must exist in ui/index.html');

class Element {
  constructor() {
    this.textContent = '';
    this.checked = true;
    this.disabled = false;
  }
}

const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, new Element());
  return elements.get(id);
};

const statusRequests = [];
const jobQueue = [];
const postedBodies = [];
let confirms = 0;

const ctx = vm.createContext({
  $,
  confirm: () => { confirms++; return true; },
  api: (path, body) => {
    assert.equal(path, '/api/headset');
    if (!body) return new Promise((resolve, reject) => statusRequests.push({resolve, reject}));
    postedBodies.push(body);
    return Promise.resolve({job: `headset-${body.action}`});
  },
  runJob: (_title, kind, send) => {
    assert.equal(kind, 'headset-panel');
    return new Promise((resolve, reject) => jobQueue.push({resolve, reject, send}));
  },
});

vm.runInContext(html.slice(start, end), ctx);

const actionIds = ['headsetInstall', 'headsetUpdate', 'headsetOpen', 'headsetClose', 'headsetRemove'];
const allDisabled = () => actionIds.every(id => $(id).disabled);
const frameState = (overrides = {}) => ({
  os: '3.7',
  arch: 'aarch64',
  variant: 'vr',
  managed: true,
  version: 'abc123',
  running: true,
  overlayRegistered: true,
  ...overrides,
});
const load = async (overrides = {}) => {
  const promise = vm.runInContext('loadHeadsetPanel()', ctx);
  statusRequests.shift().resolve(frameState(overrides));
  await promise;
};

// Loading immediately clears previous status and locks all actions.
await load();
assert.equal($('headsetUpdate').disabled, false);
assert.equal($('headsetClose').disabled, false);
const pendingLoad = vm.runInContext('loadHeadsetPanel()', ctx);
assert.equal($('headsetState').textContent, 'Checking…');
assert.ok(allDisabled(), 'buttons must disable while status refresh is in flight');
await $('headsetUpdate').onclick();
await $('headsetRemove').onclick();
assert.equal(jobQueue.length, 0, 'clicks during refresh must not start jobs');
assert.equal(confirms, 0);
statusRequests.shift().reject(Error('headset unreachable'));
await pendingLoad;
assert.equal($('headsetState').textContent, 'headset unreachable');
assert.ok(allDisabled(), 'failed status must keep all buttons disabled');

// Incomplete status fails closed instead of unlocking Install.
const incompleteLoad = vm.runInContext('loadHeadsetPanel()', ctx);
statusRequests.shift().resolve({arch: 'aarch64', variant: 'vr'});
await incompleteLoad;
assert.match($('headsetState').textContent, /incomplete/i);
assert.ok(allDisabled());

// Out-of-order status responses: newer failure beats older success, newer success beats older failure.
let older = vm.runInContext('loadHeadsetPanel()', ctx);
let newer = vm.runInContext('loadHeadsetPanel()', ctx);
let oldReq = statusRequests.shift(), newReq = statusRequests.shift();
newReq.reject(Error('latest offline'));
await newer;
oldReq.resolve(frameState({version: 'stale'}));
await older;
assert.equal($('headsetState').textContent, 'latest offline');
assert.ok(allDisabled());

older = vm.runInContext('loadHeadsetPanel()', ctx);
newer = vm.runInContext('loadHeadsetPanel()', ctx);
oldReq = statusRequests.shift();
newReq = statusRequests.shift();
newReq.resolve(frameState({version: 'fresh-v2'}));
await newer;
oldReq.reject(Error('obsolete error'));
await older;
assert.match($('headsetState').textContent, /fresh-v2/);
assert.equal($('headsetUpdate').disabled, false);

// Unmanaged running service must not enable or allow Close.
await load({managed: false, running: true});
assert.equal($('headsetClose').disabled, true, 'Close requires a managed headset panel');
assert.equal($('headsetInstall').disabled, false);
await $('headsetClose').onclick();
await $('headsetUpdate').onclick();
await $('headsetOpen').onclick();
await $('headsetRemove').onclick();
assert.equal(jobQueue.length, 0);
assert.equal(confirms, 0);

// Opt-in acknowledgement gates Install/Update/Remove, but Open and Close work without it.
await load({managed: true, running: true});
$('headsetAck').checked = false;
$('headsetAck').onchange();
assert.equal($('headsetInstall').disabled, true);
assert.equal($('headsetUpdate').disabled, true);
assert.equal($('headsetRemove').disabled, true);
assert.equal($('headsetOpen').disabled, false);
assert.equal($('headsetClose').disabled, false);
await $('headsetUpdate').onclick();
await $('headsetRemove').onclick();
assert.equal(jobQueue.length, 0, 'unacknowledged Update/Remove must not start jobs');
assert.equal(confirms, 0);

// Running job locks all actions through runJob AND the trailing status refresh.
$('headsetAck').checked = true;
$('headsetAck').onchange();
const updating = $('headsetUpdate').onclick();
assert.equal(jobQueue.length, 1);
assert.ok(allDisabled(), 'all headset buttons stay disabled while a job runs');

// Overlapping clicks, ack toggles, and manual refreshes cannot start another job or unlock buttons.
await $('headsetUpdate').onclick();
await $('headsetRemove').onclick();
await $('headsetClose').onclick();
await $('headsetOpen').onclick();
assert.equal(jobQueue.length, 1);
assert.equal(confirms, 0);
$('headsetAck').checked = false;
$('headsetAck').onchange();
$('headsetAck').checked = true;
$('headsetAck').onchange();
assert.ok(allDisabled(), 'acknowledgement toggle must not unlock during a job');
await load();
assert.ok(allDisabled(), 'manual refresh must not unlock during a job');

// Job sends expected payload and stays locked through trailing status refresh.
await jobQueue[0].send();
assert.deepEqual({...postedBodies.at(-1)}, {action: 'update', acknowledged: true});
jobQueue.shift().resolve({});
await new Promise(resolve => setImmediate(resolve));
assert.equal(statusRequests.length, 1, 'job completion must trigger trailing status refresh');
assert.ok(allDisabled(), 'buttons stay disabled during trailing refresh');
await $('headsetRemove').onclick();
assert.equal(jobQueue.length, 0);
statusRequests.shift().resolve(frameState({version: 'updated-v3'}));
await updating;
assert.match($('headsetState').textContent, /updated-v3/);
assert.equal($('headsetUpdate').disabled, false);
assert.equal($('headsetClose').disabled, false);

// Failed job still refreshes status and releases busy guard in finally.
const failingOpen = $('headsetOpen').onclick();
assert.equal(jobQueue.length, 1);
assert.ok(allDisabled());
jobQueue.shift().reject(Error('steamvr busy'));
await assert.rejects(async () => {
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(statusRequests.length, 1, 'failed job must still refresh status');
  statusRequests.shift().reject(Error('status lost'));
  await failingOpen;
}, /steamvr busy/);
assert.equal($('headsetState').textContent, 'status lost');
assert.ok(allDisabled(), 'failed trailing refresh keeps actions disabled');

// After a fresh load, recovery Remove works even on a non-VR host if managed & acknowledged.
await load({arch: 'x86_64', variant: 'desktop', managed: true, running: false});
assert.equal($('headsetInstall').disabled, true);
assert.equal($('headsetUpdate').disabled, true);
assert.equal($('headsetOpen').disabled, true);
assert.equal($('headsetClose').disabled, true);
assert.equal($('headsetRemove').disabled, false);
const removing = $('headsetRemove').onclick();
assert.equal(confirms, 1);
assert.equal(jobQueue.length, 1);
assert.ok(allDisabled());
await jobQueue[0].send();
assert.deepEqual({...postedBodies.at(-1)}, {action: 'remove', acknowledged: true});
jobQueue.shift().resolve({});
await new Promise(resolve => setImmediate(resolve));
// A newer manual refresh during trailing refresh keeps buttons disabled until the newer refresh resolves.
const trailingRefresh = statusRequests.shift();
const manualRefresh = vm.runInContext('loadHeadsetPanel()', ctx);
const newerRefresh = statusRequests.shift();
trailingRefresh.resolve(frameState({managed: true, running: false}));
await removing;
assert.ok(allDisabled(), 'superseded trailing refresh must not unlock before newer refresh settles');
newerRefresh.resolve(frameState({managed: false, running: false}));
await manualRefresh;
assert.equal($('headsetInstall').disabled, false);
assert.equal($('headsetRemove').disabled, true);
