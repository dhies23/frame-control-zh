/* Private user catalogs. Loading this module never fetches game content. */
(function(root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function sourceRows(sources, busy) {
    return sources.map(s => `<div class="nxnative-source"><div><strong>${escape(s.name)}</strong><p class="sub">${escape(s.location || '')}</p>${s.error ? `<p class="sub warn">${escape(s.error)}</p>` : ''}</div><button type="button" class="small" data-nx-remove="${escape(s.id)}" ${busy ? 'disabled' : ''} aria-label="Remove ${escape(s.name)}">移除</button></div>`).join('') || '<p class="sub">还没有游戏来源。请在上方添加目录或文件夹。</p>';
  }
  function gameRows(games, query, busy) {
    const q = query.trim().toLocaleLowerCase();
    const shown = games.filter(g => `${g.name} ${g.title_id || ''} ${g.version || ''}`.toLocaleLowerCase().includes(q));
    return shown.map(g => `<div class="nxnative-game"><div><strong>${escape(g.name)}</strong><p class="sub">${escape([g.version, g.title_id].filter(Boolean).join(' · '))}</p><p class="sub">${escape(g.installable ? '已准备好的 NxNative 包' : g.reason || '需要在受支持的构建电脑上准备。')}</p></div><button type="button" class="small ${g.installable ? 'action' : ''}" ${g.installable ? `data-nx-install="${escape(g.id)}" data-nx-source="${escape(g.source_id)}"` : 'data-nx-prepare'} ${busy ? 'disabled' : ''}>${g.installable ? '安装' : '准备步骤'}</button></div>`).join('') || `<p class="sub">${q ? '没有符合搜索条件的游戏。' : '来自游戏来源的游戏会显示在这里。'}</p>`;
  }
  function guideSection(guide) {
    return `<h3>${escape(guide.title || '在 Mac 上准备')}</h3><ol>${(guide.steps || []).map(step => `<li>${escape(step)}</li>`).join('')}</ol>${guide.command ? `<pre style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(guide.command)}</pre>` : ''}${guide.requirements?.length ? `<p class="sub">Needs: ${guide.requirements.map(escape).join(' · ')}</p>` : ''}`;
  }
  function mount({document, api, onInstalled = () => {}, onInstallApk = () => {}, mobile = () => false, sleep = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
    const get = id => document.getElementById(id);
    const state = {sources:[], games:[], busy:false, loaded:false};
    const status = message => { get('nxnativeStatus').textContent = message || ''; };
    function render() {
      get('nxnativeSources').innerHTML = sourceRows(state.sources, state.busy);
      get('nxnativeGames').innerHTML = gameRows(state.games, get('nxnativeSearch').value, state.busy);
      get('nxnativeRefresh').disabled = state.busy;
      for (const element of get('nxnativeAdd').elements) element.disabled = state.busy;
    }
    async function load() {
      const data = await api('/api/nxnative');
      state.sources = data.sources || []; state.games = data.games || []; state.loaded = true;
      if (data.preparation) {
        const guide = data.preparation;
        get('nxnativeGuide').innerHTML = guideSection(guide) + (guide.love ? guideSection(guide.love) + '<button type="button" class="small" data-nx-install-apk>安装已准备好的 APK</button>' : '');
      }
      render(); return data;
    }
    async function action(body, label) {
      if (state.busy) return;
      state.busy = true; render(); status(label + '…');
      let success = false;
      try {
        let result = await api('/api/nxnative', body);
        if (result.job) {
          const id = result.job;
          for (;;) {
            const job = await api('/api/job?id=' + encodeURIComponent(id));
            status(job.stage || job.message || label + '…');
            if (job.done) {
              if (job.error) throw new Error(job.error);
              result = job.result || job;
              break;
            }
            await sleep(1000);
          }
        }
        status(result.message || (body.action === 'add' ? '已添加游戏来源。' : body.action === 'refresh' ? '游戏来源已刷新。' : '已完成。'));
        await load();
        if (body.action === 'install') onInstalled(result);
        success = true;
      } catch (error) { status(error.message); }
      finally { state.busy = false; render(); }
      return success;
    }
    async function open() {
      get('installChooser')?.close();
      if (mobile()) get('nxnativePathNote').textContent = '使用 HTTPS 目录，或 Frame 上已挂载的文件夹。这里的路径指向 Frame，而不是你的手机或平板。';
      if (!get('nxnativeDlg').open) get('nxnativeDlg').showModal();
      if (state.busy) return;
      status('正在读取游戏来源…');
      try { await load(); status(''); } catch (error) { status(error.message); }
    }
    document.querySelectorAll('[data-open-nxnative]').forEach(button => { button.onclick = open; });
    get('nxnativeClose').onclick = () => get('nxnativeDlg').close();
    get('nxnativeRefresh').onclick = () => action({action:'refresh'}, '刷新游戏来源');
    get('nxnativeSearch').oninput = render;
    get('nxnativeGuide').onclick = event => {
      if (state.busy || !event.target.closest('[data-nx-install-apk]')) return;
      get('nxnativeDlg').close();
      onInstallApk();
    };
    get('nxnativeAdd').onsubmit = async event => {
      event.preventDefault();
      const form = get('nxnativeAdd');
      const location = form.elements.namedItem('location').value.trim();
      const name = form.elements.namedItem('name').value.trim();
      if (await action({action:'add', location, name}, '添加游戏来源')) form.reset();
    };
    get('nxnativeSources').onclick = event => {
      const button = event.target.closest('[data-nx-remove]');
      if (button) action({action:'remove', source:button.dataset.nxRemove}, 'Remove game source');
    };
    get('nxnativeGames').onclick = event => {
      if (event.target.closest('[data-nx-prepare]')) {
        const guide = get('nxnativePreparation'); guide.open = true; guide.scrollIntoView?.({block:'nearest'}); return;
      }
      const button = event.target.closest('[data-nx-install]');
      if (button && !button.disabled) action({action:'install', source:button.dataset.nxSource, id:button.dataset.nxInstall}, 'Install NxNative game');
    };
    return {open, load, action, state};
  }
  const exported = {mount, sourceRows, gameRows};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  else root.FrameNxNative = exported;
})(typeof window !== 'undefined' ? window : globalThis);
