// Uses index.html's api(), which sends the X-Frame-UI key every /api call needs.
(() => {
  const get=id=>document.getElementById(id), status=get('steamGridStatus');
  function show(data) {
    status.textContent=data.environment?'SteamGridDB 密钥由环境变量设置。':
      data.steamgriddb_configured?'SteamGridDB 密钥已保存。':'未配置密钥。原始图片与生成的封面图已启用。';
  }
  async function save(value) {
    try {show(await api('/api/settings/artwork',{steamgriddb_api_key:value}));get('steamGridKey').value='';}
    catch(e) {status.textContent=e.message;}
  }
  get('saveSteamGridKey').onclick=()=>save(get('steamGridKey').value.trim());
  get('clearSteamGridKey').onclick=()=>save('');
  get('refreshAndroidArt').onclick=async()=>{
    const button=get('refreshAndroidArt');button.disabled=true;
    try {
      const result=await runJob('刷新库封面图','library-artwork',()=>api('/api/android',{action:'refresh-art',all:true}));
      if (result) {
        const items=[...(result.apps||[]),...(result.titles||[])];
        const failed=items.filter(a=>a.error);
        status.textContent=failed.length?`${failed.length} of ${items.length} failed: ${failed.map(a=>(a.package||a.id)+': '+a.error).join('; ')}`:
          `已刷新 ${items.length} 个应用与标题的封面图。`;
        if (typeof loadAndroid==='function') loadAndroid();
        if (typeof loadTitles==='function') loadTitles();
      }
    } catch(e) {status.textContent=e.message;}
    finally {button.disabled=false;}
  };
  api('/api/settings/artwork').then(show).catch(e=>{status.textContent=e.message;});
})();
