/* Fixed local-download operations in Steam CEF. Plugin code never runs here. */
(async function(payload) {
  // A poll may spend 15s verifying its receipt, then 25s reading Steam state.
  // Retain the subscription through slow polls and active queue batches.
  const sessionIdleMs = 60000;
  const api = window.SteamClient?.Downloads;
  let bridge = window.__frameyDownloadsBridgeV1;
  if (bridge && bridge.api !== api) {
    for (const key of [...bridge.sessions.keys()]) bridge.remove(key);
    bridge = null;
  }
  if (!bridge) {
    const sessions = new Map();
    const remove = key => {
      const item = sessions.get(key);
      if (item) item.closed = true;
      item?.handle?.unregister();
      sessions.delete(key);
      if (!sessions.size) {clearInterval(bridge.timer); bridge.timer = null;}
    };
    bridge = window.__frameyDownloadsBridgeV1 = {api, sessions, remove, timer:null, busy:false, busySession:null, inFlight:null};
  }
  if (payload.action === 'close') {bridge.remove(payload.session); return null;}
  if (typeof api?.RegisterForDownloadItems !== 'function')
    throw Error('Steam 下载订阅不可用');
  let item = bridge.sessions.get(payload.session);
  const integer = (value, min, max=Number.MAX_SAFE_INTEGER) =>
    Number.isSafeInteger(value) && value >= min && value <= max;
  const project = rows => {
    if (!Array.isArray(rows) || rows.length > 2048) throw Error('Steam 下载列表超出上限');
    const ids = new Set();
    return rows.map(row => {
      if (!integer(row?.appid,1,4294967295) || ids.has(row.appid) ||
          !integer(row.queue_index,-1,10000) || typeof row.completed !== 'boolean' ||
          !integer(row.deferred_time ?? 0,0)) throw Error('本地 Steam 下载项无效');
      ids.add(row.appid);
      const infos = row.update_type_info ?? [];
      if (!Array.isArray(infos) || infos.length > 32) throw Error('Steam 更新大小无效');
      return {appid:row.appid, queue_index:row.queue_index, completed:row.completed,
        deferred_time:row.deferred_time ?? 0, active:row.active === true, paused:row.paused === true,
        update_type_info:infos.map(info => {
          const progress = info.progress ?? [];
          if (!Array.isArray(progress) || progress.length > 32) throw Error('Steam 更新大小无效');
          return {has_update:info.has_update === true, completed_update:info.completed_update === true,
            progress:progress.map(part => {
              if (!integer(part.bytes_total ?? 0,0)) throw Error('Steam 下载大小无效');
              return {bytes_total:part.bytes_total ?? 0};
            })};
        })};
    });
  };
  if (!item) {
    if (payload.action !== 'read') throw Error('排队前请重新打开下载面板');
    if (bridge.sessions.size >= 4) throw Error('Framey 下载会话过多');
    item = {rows:[], ready:false, modern:false, version:0, changedVersion:null,
      touched:Date.now(), failure:null, handle:null};
    item.handle = api.RegisterForDownloadItems((...args) => {
      try {
        const rows = Array.isArray(args[1]) ? args[1] : args[0];
        if (!Array.isArray(rows)) throw Error('Steam 下载回调无效');
        const modern = rows.some(row => row && Object.hasOwn(row,'item_data'));
        if (modern) {
          item.modern = true;
          const local = rows.filter(row => row.remote_client_id === '0');
          if (local.length > 1) throw Error('本地 Steam 下载列表存在歧义');
          item.rows = project(local[0]?.item_data ?? []);
        } else {
          if (rows.length) item.modern = false;
          item.rows = project(rows);
        }
        item.ready = true; item.failure = null; item.version++;
      } catch (error) {item.ready = false; item.failure = error.message;}
    });
    if (typeof item.handle?.unregister !== 'function') throw Error('Steam 下载订阅失败');
    bridge.sessions.set(payload.session,item);
    if (!bridge.timer) bridge.timer = setInterval(() => {
      for (const [key,state] of bridge.sessions)
        if (bridge.busySession !== key && Date.now() - state.touched > sessionIdleMs) bridge.remove(key);
    },5000);
  }
  item.touched = Date.now();
  if (payload.action === 'read') {
    if (item.failure) throw Error(item.failure);
    return {ready:item.ready, items:item.rows, revision:item.version};
  }
  const ids = payload.appids;
  if (payload.action !== 'queue' || !Array.isArray(ids) || !ids.length || ids.length > 2048 ||
      ids.some(id => !integer(id,1,4294967295)) || new Set(ids).size !== ids.length)
    throw Error('下载队列请求无效');
  if (!item.ready || item.failure) throw Error('Steam 下载列表不可用');
  if (bridge.busy || bridge.inFlight) throw Error('另一个下载队列变更正在进行');
  if (payload.revision !== item.version || item.changedVersion === item.version)
    throw Error('下载内容已变化或正在等待 Steam 更新；请查看当前列表');
  for (const method of ['QueueAppUpdate','SetQueueIndex','ResumeAppUpdate'])
    if (typeof api[method] !== 'function') throw Error('Steam 下载队列控制不可用');
  const selected = ids.map(id => item.rows.find(row => row.appid === id));
  if (selected.some(row => !row || row.completed || row.queue_index !== -1))
    throw Error('下载内容已变化；排队前请查看当前列表');
  const size = row => row.update_type_info.reduce((sum,info) => sum +
    (info.has_update && !info.completed_update ? info.progress.reduce((n,p) => n+p.bytes_total,0) : 0),0);
  selected.sort((a,b) => size(a)-size(b));
  const end = Math.max(-1,...item.rows.map(row => row.queue_index));
  const resume = item.rows.find(row => row.queue_index === 0)?.appid ?? selected[0].appid;
  const invoke = async (name,...args) => {
    if (item.closed || Date.now() > deadline) throw Error('下载队列操作已过期，或面板已关闭');
    let timer;
    let result;
    try {
      const native = Promise.resolve(api[name](...args,...(modern ? ['0'] : [])));
      // A UI timeout cannot cancel Steam's promise. Keep replacement panels
      // from starting another mutation until that native call actually settles.
      bridge.inFlight = native;
      const settled = () => {if (bridge.inFlight === native) bridge.inFlight = null;};
      native.then(settled,settled);
      result = await Promise.race([native,
        new Promise((_,reject) => {timer = setTimeout(() => reject(Error('Steam 队列操作超时；请刷新后重试')),2000);})]);
    } finally {clearTimeout(timer);}
    if (result === false) throw Error(name+' 被 Steam 拒绝');
  };
  bridge.busy = true;
  bridge.busySession = payload.session;
  item.changedVersion = item.version;
  const modern = item.modern, deadline = Date.now()+15000;
  let queued = 0;
  try {
    for (const [index,row] of selected.entries()) {
      const current = item.rows.find(current => current.appid === row.appid);
      if (!item.ready || !current || current.completed || current.queue_index !== -1)
        throw Error('排队操作期间下载内容已变化');
      await invoke('QueueAppUpdate',row.appid);
      queued++;
      await invoke('SetQueueIndex',row.appid,end+1+index);
    }
    await invoke('ResumeAppUpdate',resume);
    return {queued};
  } catch (error) {
    throw Error('已排队 '+queued+' of '+selected.length+' downloads; '+error.message);
  } finally {
    item.touched = Date.now();
    bridge.busy = false;
    bridge.busySession = null;
  }
})
