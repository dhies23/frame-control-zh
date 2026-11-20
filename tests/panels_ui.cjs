// Execute the shipped headset page with controlled response ordering; no headset.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2] === '-' ? 0 : process.argv[2], 'utf8');

function page() {
  const elements = new Map();
  const requests = [];
  const element = () => ({textContent:'', disabled:false, children:[],
    replaceChildren() { this.children = []; }, append(child) { this.children.push(child); }});
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = {
    document:{getElementById:get, createElement:element},
    location:{hash:'#fixture-key', pathname:'/'}, history:{replaceState(){}},
    sessionStorage:{getItem(){return null;}, setItem(){}}, window:{close(){}},
    fetch:(path, options) => new Promise(resolve => requests.push({path, options, resolve})),
  };
  vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  const answer = (index, data, ok=true) => requests[index].resolve({ok, json:async () => data});
  const flush = () => new Promise(resolve => setImmediate(resolve));
  return {get, requests, answer, flush};
}

(async () => {
  // A startup response arriving after a user refresh must not resurrect old panels.
  const first = page();
  const newer = first.get('refresh').onclick();
  first.answer(1, {panels:[{key:'current', title:'Current <panel>'}]});
  await newer;
  first.answer(0, {panels:[{key:'closed', title:'Already closed'}]});
  await first.flush();
  assert.deepEqual(first.get('panels').children.map(b => b.textContent), ['Current <panel>']);
  const selected = first.get('panels').children[0].onclick();
  assert.deepEqual(JSON.parse(first.requests[2].options.body), {key:'current'});
  first.answer(2, {message:'Asked SteamVR to show the panel.'});
  await selected;
  assert.equal(first.get('status').textContent, 'Asked SteamVR to show the panel.');

  // An old failure must not cover the newer successful empty result.
  const second = page();
  const empty = second.get('refresh').onclick();
  second.answer(1, {panels:[]});
  await empty;
  second.answer(0, {error:'Old connection failed'}, false);
  await second.flush();
  assert.equal(second.get('status').textContent, 'No open panels.');

  // A late old success must not hide the most recent refresh failure.
  const third = page();
  const failed = third.get('refresh').onclick();
  third.answer(1, {error:'Current connection failed'}, false);
  await failed;
  third.answer(0, {panels:[{key:'stale', title:'Stale'}]});
  await third.flush();
  assert.equal(third.get('status').textContent, 'Current connection failed');
  assert.equal(third.get('panels').children.length, 0);
  console.log('Panel refresh response ordering: pass');
})().catch(error => { console.error(error); process.exitCode=1; });
