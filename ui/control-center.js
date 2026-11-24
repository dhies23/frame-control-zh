/* The Control Center uses the shared page's guarded api() and status poll.
 * Browsing never starts a stream, installs a helper or changes the headset. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.FrameControlCenter = factory();
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const categories = [
    ['all', '全部', 'grid'], ['device', '设备与音频', 'device'],
    ['vr', 'VR 与舒适度', 'eye'], ['apps', '应用与文件', 'apps'],
    ['network', '连接与远程', 'network'], ['companion', 'Frame Control', 'settings'],
  ];
  // Each destination is an existing workflow, with its own guards and confirmations.
  const catalog = [
    {id:'audio', category:'device', icon:'audio', title:'音频', kind:'control', label:'可在此操作',
      description:'设置头显默认输出音量，或将其静音。只有选择应用或静音后才会生效。',
      keywords:'声音 扬声器 耳机 响亮 安静', special:'audio'},
    {id:'health', category:'device', icon:'pulse', title:'电池与散热状况', kind:'read', label:'只读指标',
      description:'查看电量、带符号的电池功率、充电器读数、电池温度、健康度以及可选的容量/循环读数。过热警告使用内核自身的过热与临界阈值。',
      keywords:'电池 充电 充电器 剩余时间 功率 瓦 usb 健康度 循环 容量 磨损 发热 温度 热 传感器', special:'health', links:[['#comfort','配置电池与过热提醒']]},
    {id:'storage', category:'device', icon:'files', title:'存储与内存', kind:'read', label:'只读指标',
      description:'查看用户与系统可用存储空间以及可用内存。在现有库中管理已安装应用，或使用 SFTP 传输文件。',
      keywords:'磁盘 空间 根目录 主目录 可用 总量 内存 容量 清理 占用 存储', special:'storage', links:[['#library','已安装游戏'],['#linuxApps','Linux 应用'],['#power','SFTP 与远程工具']]},
    {id:'microphone', category:'device', icon:'audio', title:'麦克风与音频路由', kind:'headset', label:'在头显中',
      description:'Frame Control 只能控制默认输出音量。麦克风静音、输入增益、输出选择、声道平衡和按应用混音在这里没有经过验证的 API。',
      keywords:'麦克风 输入 音频 输出 设备 增益 静音 均衡器 空间音频 平衡 耳机',
      guidance:'这些调整请在头显的音频控制或应用自身的音频设置中完成。'},
    {id:'power', category:'device', icon:'power', title:'电源与休眠', kind:'flow', label:'现有控制',
      description:'休眠、重启或关机需要确认并输入开发者模式密码。休眠中的 Frame 必须在设备上唤醒。',
      keywords:'电池 挂起 重启 关机 唤醒 空闲 超时 保持唤醒 抑制', links:[['#power','电源控制']],
      guidance:'在头显中设置自动休眠：Steam 设置 → 电源 → 空闲后休眠。保持唤醒可通过现有的脚本/MCP 工具实现，这里没有开关。'},
    {id:'system', category:'device', icon:'device', title:'SteamOS 与硬件', kind:'headset', label:'在头显中',
      description:'在上方查看当前系统版本。系统更新、开发者模式和硬件设置请在 Frame 的 Steam 设置中管理。',
      keywords:'固件 更新 测试版 稳定版 通道 开发者 密码 蓝牙 手柄 电池 配对 校准 亮度 刷新率 显示 IPD 镜片 透视 相机 眼动 手部追踪 注视点渲染 恢复 恢复出厂设置',
      guidance:'亮度、刷新率、光学、手柄、蓝牙和透视在这里没有经过验证的远程控制。请在头显自带的控制中调整。系统恢复和恢复出厂设置属于手动操作。'},
    {id:'performance', category:'vr', icon:'pulse', title:'性能监视器', kind:'read', label:'只读指标',
      description:'CPU、GPU 频率以及应用与合成器的耗时数据来自 Frame。不可用的耗时保持空白，包括 SteamVR 处于待机时。',
      keywords:'fps 帧率 gpu cpu 发热 温度 热 传感器 渲染 内存', special:'performance', links:[['#vrTools','HUD 与性能工具']]},
    {id:'view', category:'vr', icon:'eye', title:'头显画面与投屏', kind:'flow', label:'现有控制',
      description:'截取镜片画面或桌面、串流实时视频，并控制聚焦的面板。投屏可能暴露隐私内容。',
      keywords:'截图 相机 视频 采集 实时 串流 桌面 镜像 控制 眼睛', links:[['#view','打开头显画面'],['#shots','截图']]},
    {id:'comfort', category:'vr', icon:'clock', title:'家庭与舒适', kind:'flow', label:'现有控制',
      description:'设置会话时限、休息和确认提醒，并带电池与过热提醒。即使伴侣端断开连接，头显内的提醒仍会继续。',
      keywords:'家长 儿童 计时 分钟 通知 健康 休息 低电量', links:[['#comfort','配置会话']],
      guidance:'这是提醒，不是家长锁。请在会话结束返回 Steam 主界面前保存并暂停。'},
    {id:'playspace', category:'vr', icon:'space', title:'游玩空间与移动', kind:'headset', label:'在头显中',
      description:'使用 SteamVR 的重置视角和房间设置。坐姿游玩、快速转身、传送和暗角请在各游戏自己的设置中选择。',
      keywords:'边界 防护墙 追踪 原点 地面 站立 坐姿 晕动 移动 转身 重置 房间',
      guidance:'没有经过验证的通用移动开关，也没有远程追踪原点控制。'},
    {id:'panels', category:'vr', icon:'grid', title:'面板与工作区', kind:'flow', label:'现有控制',
      description:'找到已打开的面板并把它移到前面，或在头显中打开面板切换器。面板的移动、缩放和停靠请用 SteamVR 仪表盘。',
      keywords:'叠加 窗口 布局 浮动 世界 曲面 停靠 影院 多任务', links:[['#panels','打开面板切换器']],
      guidance:'不支持保存的空间布局。面板摆放仍由 SteamVR 管理。'},
    {id:'steam', category:'apps', icon:'game', title:'Steam 库', kind:'flow', label:'现有控制',
      description:'浏览已拥有的游戏和 Frame 评级、查看安装进度，或启动已安装的游戏。购买和许可提示由 Steam 处理。',
      keywords:'游戏 下载 安装 商店 已拥有 兼容性 评级 已验证 proton', links:[['#library','已安装游戏'],['#getgames','库与商店']]},
    {id:'forcequit', category:'apps', icon:'power', title:'强制退出游戏', kind:'control', label:'可在此操作',
      description:'查看 Frame 上正在运行的内容，并退出已无响应的游戏或应用。会先请求 Steam 处理；如果游戏仍未关闭，就停止它自己的进程。未保存的进度会丢失。',
      keywords:'卡死 无响应 冻结 崩溃 卡住 结束 关闭 停止 结束任务 运行中 退出', special:'forcequit'},
    {id:'android', category:'apps', icon:'apps', title:'Android 应用', kind:'flow', label:'现有控制',
      description:'发现应用、管理 Lepton 实例，并启动、停止、测试或移除已安装应用。安装前可查看兼容性报告。',
      keywords:'apk fdroid lepton 应用 侧载 来源 仓库 报告', links:[['#appStore','发现应用'],['#androidLibrary','管理已安装应用']]},
    {id:'sources', category:'apps', icon:'shield', title:'应用来源与兼容性', kind:'flow', label:'现有控制',
      description:'在来源中选择目录源，或添加带签名的 F-Droid 仓库。查看具体应用的安装可用性和兼容性报告。',
      keywords:'fdroid f-droid 仓库 来源 指纹 信任 签名 兼容性 报告 apk sidequest 数据 备份 还原', links:[['#appStore','目录与来源'],['#androidLibrary','应用与报告']],
      guidance:'Android 数据备份和还原只能通过脚本完成。此界面不提供应用数据恢复流程。'},
    {id:'display', category:'apps', icon:'display', title:'Android 显示与文字', kind:'flow', label:'现有控制',
      description:'选择一个运行中的 Android 实例，然后设置分辨率、界面密度和文字大小，或恢复其默认值。',
      keywords:'dpi 缩放 字体 缩放 清晰 原生 1920 1080 2560 1440 分辨率 屏幕 文字 无障碍', links:[['#display','配置应用显示']],
      guidance:'只对该 Lepton 实例生效，不影响头显镜片。重新启动后是否保留尚未验证。'},
    {id:'sideload', category:'apps', icon:'game', title:'侧载游戏', kind:'flow', label:'现有控制',
      description:'发送游戏文件夹、ZIP 或可执行文件。Frame Control 会选择 Proton 或 Linux 运行时，并添加 Steam 库条目。',
      keywords:'windows exe zip linux devkit 运行时 快捷方式 启动 移除', links:[['#sideloaded','管理游戏'],['#transfer','发送游戏']]},
    {id:'files', category:'apps', icon:'files', title:'发送文件', kind:'flow', label:'现有控制',
      description:'把文件发送到 ~/Downloads，或把游戏发送到 Steam 库。双向剪贴板文字见头显输入。',
      keywords:'上传 传输 下载 sftp 存储', links:[['#transfer','发送文件'],['#pad','剪贴板']]},
    {id:'media', category:'apps', icon:'play', title:'视频与立体照片', kind:'flow', label:'预览功能',
      description:'发送并用内置播放器播放 H.264/H.265 视频、SBS/OU 照片或小型 splat 场景。可选择布局和影院环绕。',
      keywords:'电影 照片 sbs ou 3d splat 高斯 影院 媒体 播放器', links:[['#media','打开媒体播放器']],
      guidance:'尚不支持拖动进度、字幕、原生 HEIC/MPO 和大型 splat 场景。'},
    {id:'flatpak', category:'apps', icon:'apps', title:'Linux 桌面应用', kind:'flow', label:'现有控制',
      description:'按 Flathub ID 安装用户 Flatpak，选择 VLC、Moonlight 等快捷项，并管理已安装应用。',
      keywords:'flatpak firefox vlc remmina moonlight flathub 安装 移除', links:[['#linuxApps','管理 Linux 应用']]},
    {id:'connection', category:'network', icon:'network', title:'头显与地址', kind:'flow', label:'现有控制',
      description:'管理头显、局域网、USB-C、mDNS 和 Tailscale 地址。查看连接尝试记录，选择要控制的设备。',
      keywords:'wifi wi-fi 网络 ip ssh 配对 密钥 发现 别名 tailscale usb 离线 重新连接', links:[['#devices','管理头显','desktop'],['#connection','连接详情']],
      guidance:'在头显上加入 Wi-Fi 网络。Frame Control 管理连接地址，不会修改 Wi-Fi 凭据。'},
    {id:'remote', category:'network', icon:'display', title:'远程访问', kind:'flow', label:'现有控制',
      description:'使用主机现有的工具打开 SSH、SFTP、Steam Link 或远程桌面。开发者模式会启用头显的 SSH 和 xrdp。',
      keywords:'终端 shell 远程桌面 rdp xrdp 串流 steam link sftp', links:[['#power','打开远程工具']]},
    {id:'input', category:'network', icon:'keyboard', title:'头显输入', kind:'flow', label:'现有控制',
      description:'用这台电脑的键盘和鼠标在 Frame 的应用中输入和指点，并在两侧剪贴板之间双向复制文字。在任何界面按 T 即可打开。',
      keywords:'鼠标 指针 键盘 触控板 触摸 点击 kde connect 输入 打字 文字 剪贴板 复制 粘贴 发送',
      links:[['#pad','头显输入'],['#view','面板控制']],
      guidance:'首次使用时会在 Frame 上配置 KDE Connect。剪贴板功能需要头显中打开桌面面板。'},
    {id:'mac', category:'network', icon:'display', title:'头显中的 Mac', kind:'flow', label:'仅 macOS', platform:'mac',
      description:'把 Mac 屏幕或单个窗口串流到头显面板。可选择画质，并让每个窗口各自拥有独立显示。',
      keywords:'macos mac 窗口 屏幕 共享 串流 画质 清晰 均衡 轻量 权限', links:[['#macview','配置 Mac 串流']],
      guidance:'需要在 Mac 上运行 Frame Control。服务器运行在 Windows、Linux 或头显上时不可用。'},
    {id:'privacy', category:'companion', icon:'shield', title:'隐私与共享', kind:'flow', label:'伴侣端设置',
      description:'分别选择匿名使用统计、兼容性共享和错误详情。可查看已发送的内容，并管理可选的联络邮箱。',
      keywords:'遥测 分析 诊断 同意 数据 跟踪 邮箱 联络 退出', links:[['#privacy','隐私设置']]},
    {id:'artwork', category:'companion', icon:'image', title:'库封面图', kind:'flow', label:'伴侣端设置',
      description:'配置可选的 SteamGridDB 密钥或刷新封面图。没有密钥时，源图片和生成的图片仍可用。',
      keywords:'cover image artwork 封面 图片 steamgriddb api key 密钥 library 库 art 图 refresh 刷新', links:[['#artworkSettings','封面图设置']]},
    {id:'assistant', category:'companion', icon:'spark', title:'助手与智能体', kind:'flow', label:'可选工具',
      description:'使用你自己的模型端点，审阅建议的操作。在你选择启用前，不会向模型发送任何内容；受保护的操作需要批准。',
      keywords:'ai mcp 模型 端点 智能体 批准 聊天 助手', links:[['/assistant','打开助手']]},
    {id:'support', category:'companion', icon:'help', title:'更新与诊断', kind:'flow', label:'配套设置',
      description:'在桌面应用中检查 Frame Control 更新，或生成私密的问题报告。发送前请先查看诊断预览。',
      keywords:'应用 版本 问题 反馈 支持 更新 日志 错误 报告 许可', links:[['#privacy','更新与问题报告']]},
  ];
  const paths = {
    grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    device:'M3 7h18v10H3z M8 11v2 M16 11v2', eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
    apps:'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M17 14v6 M14 17h6',
    network:'M2 8a16 16 0 0 1 20 0 M5 12a11 11 0 0 1 14 0 M8 16a6 6 0 0 1 8 0 M12 20h.01',
    settings:'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
    audio:'M3 9h4l5-4v14l-5-4H3z M16 8a6 6 0 0 1 0 8 M19 5a10 10 0 0 1 0 14',
    power:'M12 2v10 M6 5a9 9 0 1 0 12 0', pulse:'M2 12h5l3-8 4 16 3-8h5',
    clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2',
    space:'M3 8l9-5 9 5v8l-9 5-9-5z M3 8l9 5 9-5 M12 13v8',
    game:'M6 8h12l3 9-3 2-4-4h-4l-4 4-3-2z M7 10v4 M5 12h4 M16 11h.01 M18 13h.01',
    display:'M3 4h18v13H3z M8 21h8 M12 17v4', files:'M3 5h7l2 3h9v12H3z',
    play:'M8 4l12 8-12 8z', keyboard:'M2 6h20v12H2z M6 10h.01 M10 10h.01 M14 10h.01 M18 10h.01 M6 14h12',
    shield:'M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6z M8 12l3 3 5-6',
    image:'M3 3h18v18H3z M3 17l6-6 5 5 3-3 4 4 M16 7h.01',
    spark:'M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z',
    help:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5 M12 17h.01',
  };
  const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.settings}"/></svg>`;
  function matches(item, query, category) {
    const text = [item.title, item.description, item.keywords, item.guidance, item.label,
      categories.find(([id])=>id === item.category)?.[1]].join(' ').toLowerCase();
    return (category === 'all' || item.category === category) && query.toLowerCase().trim().split(/\s+/).every(word => text.includes(word));
  }
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const KINDS = {steam:'Steam 游戏', devkit:'侧载游戏', shortcut:'非 Steam 快捷方式', android:'Android 应用'};
  const number = (value, suffix = '', digits = 0) => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits:digits}) + suffix : '不可用';
  const gib = value => Number.isFinite(value) ? number(value / 1073741824, ' GiB', 1) : '不可用';
  const duration = seconds => Number.isFinite(seconds) && seconds >= 0 ? seconds < 3600 ? `${Math.floor(seconds / 60)} min` : `${Math.floor(seconds / 3600)} h ${Math.floor(seconds % 3600 / 60)} min` : '不可用';
  function metrics(sample) {
    const s = sample || {}, b = s.battery || {}, p = s.performance || {}, h = s.disk?.home, m = s.memory;
    const capacity = b.percent;
    const idle = !!s.power && Number.isFinite(b.watts) && Math.abs(b.watts) < .5;
    const charging = b.status === 'Charging';
    const full = b.status === 'Full';
    const batteryState = !sample ? '连接后可读取电量和功率' : !s.battery ? '电池传感器不可用' :
      full ? '已充满' : charging ? '充电中' : b.status === 'Not charging' || idle ? (s.power ? '已插电 · 未充电' : '未充电') : b.status === 'Discharging' ? (s.power ? '已插电 · 电池供电' : '电池供电') : '充电状态不可用';
    return {
      battery:number(capacity, '%'), batteryDetail:batteryState,
      storage:h ? gib(h.free) : '不可用', storageDetail:h ? `${gib(h.total)} 总容量 · 用户存储` : '用户存储不可用',
      memory:m && Number.isFinite(m.total) && Number.isFinite(m.available) ? gib(m.total - m.available) : '不可用',
      memoryDetail:m ? `${gib(m.total)} 总容量 · 系统内存已用` : '内存传感器不可用',
      rootFree:gib(s.disk?.root?.free), rootTotal:gib(s.disk?.root?.total), memoryAvailable:gib(m?.available),
      fps:number(p.compositorFps, '', 1), fpsDetail:'合成器 FPS · 非游戏 FPS',
      power:number(b.watts, ' W', 1), charger:s.power ? `${s.power.type || '已连接'} · ${number(s.power.watts, ' W', 1)}` : sample ? '未报告充电器' : '不可用',
      batteryTemp:number(b.tempC, ' °C', 1), batteryHealth:b.health && b.health !== 'Unknown' ? b.health : '不可用',
      batterySource:b.source || '不可用', cycles:number(b.cycleCount),
      fullCapacity:number(b.fullMah, ' mAh', 1), designCapacity:number(b.designMah, ' mAh', 1),
      capacityHealth:number(b.capacityHealthPercent, '%', 1),
      estimate:charging ? (b.timeToFull > 0 ? `${duration(b.timeToFull)} 后充满` : '不可用') : b.status === 'Discharging' && !idle && b.timeToEmpty > 0 && b.timeToEmpty < 86400 ? `${duration(b.timeToEmpty)} remaining` : '不可用',
      wifi:s.wifi?.ssid || '不可用', signal:number(s.wifi?.signal, '%'), ip:s.ip || '不可用',
      uptime:duration(s.uptime), os:s.os?.version || '不可用', build:s.os?.build || '不可用',
      temperature:number(s.temp, ' °C', 1), cpu:number(p.cpuPercent, '%', 1), gpu:number(p.gpuMHz, ' MHz', 1),
      appFps:number(p.appFps, '', 1), frame:number(p.frameMs, ' ms', 2), render:number(p.gpuMs, ' ms', 2), compositorCpu:number(p.compositorCpuMs, ' ms', 2),
      thermal:!Array.isArray(s.thermalAlerts) ? '过温保护数据不可用' : s.thermalAlerts.length ? s.thermalAlerts.map(a => `${a.zone || '传感器'} ${number(a.tempC, ' °C')} ≥ ${number(a.limitC, ' °C')}`).join('; ') : '未报告过热或危险温度保护',
    };
  }
  // Strict validation and captured device identity apply even if a stale enabled
  // button is clicked during a disconnect, headset switch, or pending request.
  function createController({api, getContext}) {
    let busy = false, quitting = false;
    const connected = message => {
      const context = getContext();
      if (context.online !== true) throw new Error(message);
      return context;
    };
    return {
      get busy() { return busy; },
      get quitting() { return quitting; },
      async running() {
        const context = connected('连接 Frame 后可查看正在运行的内容。');
        const result = await api('/api/running', undefined, context.device);
        return getContext().generation === context.generation ? result : null;
      },
      async forceQuit(app) {
        if (quitting) throw new Error('正在强制退出，请稍候。');
        if (!/^\d{1,10}$/.test(String(app?.appid))) throw new Error('请选择要退出的运行中游戏。');
        const context = connected('退出游戏前请先连接 Frame。');
        quitting = true;
        try {
          const result = await api('/api/running', {action:'quit', appid:String(app.appid)}, context.device);
          return getContext().generation === context.generation ? result : null;
        } finally { quitting = false; }
      },
      async audio(change) {
        if (busy) throw new Error('音频修改正在进行中。');
        const context = getContext();
        if (context.online !== true || !context.fresh || !context.sample?.volume) throw new Error('修改音频前请先连接并刷新 Frame。');
        const keys = Object.keys(change);
        if (!keys.length || keys.some(k => !['level','muted'].includes(k)) ||
            ('level' in change && (typeof change.level !== 'number' || !Number.isFinite(change.level) || change.level < 0 || change.level > 1)) ||
            ('muted' in change && typeof change.muted !== 'boolean')) throw new Error('音量需在 0 到 100% 之间，静音状态需有效。');
        busy = true;
        try {
          const result = await api('/api/volume', change, context.device);
          if (getContext().generation !== context.generation) return null;
          return result;
        } finally { busy = false; }
      },
    };
  }
  function mount({document, api, refresh, refreshAudio = refresh, getContext, openConnection, platform, confirm = text => globalThis.confirm(text)}) {
    const get = id => document.getElementById(id), page = get('controlCenter');
    let category = 'all', sample = null, receivedAt = null, available = false, error = '', dirty = false, sampleGeneration = null, audioPending = false;
    const metric = (id, label) => `<div><dt>${label}</dt><dd data-cc-metric="${id}">不可用</dd></div>`;
    const perf = `<dl class="cc-readings">${[['appFps','应用 FPS'],['frame','合成器周期'],['render','渲染 GPU 时间'],['compositorCpu','合成器 CPU'],['cpu','系统 CPU'],['gpu','GPU 频率'],['temperature','最热传感器']].map(([id,label])=>metric(id,label)).join('')}</dl><p class="cc-note">GPU 时间指渲染耗时，不是占用率。最热的传感器未必是 CPU 温度。</p><p class="cc-note" data-cc-metric="thermal">过温保护数据不可用</p>`;
    const audio = `<div class="cc-audio"><label for="ccVolume">输出音量 <output id="ccVolumeValue" for="ccVolume">—</output></label><input id="ccVolume" type="range" min="0" max="100" step="1" value="0" disabled><div class="cc-audio-actions"><button class="action small" id="ccApplyVolume" disabled>应用音量</button><button class="small" id="ccMute" disabled>静音</button><span id="ccAudioReadback" class="cc-note">等待音频读数</span></div><p class="cc-note" id="ccAudioResult" role="status"></p></div>`;
    const batteryNote = '只读。不可用表示未上报、无法读取或数值无效。健康度是驱动给出的状态；满电容量 / 设计容量是电量计的估算值，不是诊断结论。Frame 不支持充电上限。';
    const health = `<dl class="cc-readings">${[['battery','电量'],['batteryDetail','充电状态'],['power','电池功率 · 带符号'],['charger','充电器'],['estimate','电池估算'],['batteryTemp','电池温度'],['batteryHealth','电池健康度'],['cycles','充电次数'],['fullCapacity','满电容量'],['designCapacity','设计容量'],['capacityHealth','满电 / 设计容量'],['batterySource','电池来源']].map(([id,label])=>metric(id,label)).join('')}</dl><p class="cc-note">${batteryNote}</p>`;
    const forcequit = `<div class="cc-quit"><div class="cc-audio-actions"><button class="small" type="button" id="ccRunningCheck">查看运行中内容</button></div><ul class="cc-running" id="ccRunningList"></ul><p class="cc-note" id="ccRunningResult" role="status"></p></div>`;
    const storage = `<dl class="cc-readings">${[['storage','用户存储可用'],['rootFree','系统存储可用'],['rootTotal','系统存储总量'],['memoryAvailable','可用内存']].map(([id,label])=>metric(id,label)).join('')}</dl>`;
    get('ccCategories').innerHTML = categories.map(([id,label,symbol])=>`<button type="button" data-cc-category="${id}" aria-pressed="${id === 'all'}">${icon(symbol)}<span>${label}</span><span class="cc-category-count"></span></button>`).join('');
    const specials = {audio, performance:perf, health, storage, forcequit};
    get('ccCards').innerHTML = catalog.map(item=>`<section class="cc-card" data-cc-card="${item.id}"><div class="cc-card-head"><span class="cc-icon">${icon(item.icon)}</span><span class="cc-kind cc-kind-${item.kind}">${item.label}</span></div><h2>${item.title}</h2><p>${item.description}</p>${specials[item.special] || ''}${item.guidance ? `<div class="cc-guidance">${item.guidance}</div>` : ''}${item.links ? `<div class="cc-links">${item.links.map(([href,label,desktop])=>`<a href="${href}"${desktop ? ' class="desk-only"' : ''}${item.platform === 'mac' ? ' data-cc-mac-link' : ''}>${label}<span aria-hidden="true">↗</span></a>`).join('')}</div>` : ''}</section>`).join('');
    // Host-only features stay searchable with a reason; only their unsupported
    // action disappears. The host API, rather than viewport/UA, decides availability.
    let macSupported = platform === 'darwin';
    const visibleCatalog = catalog;
    function filter() {
      const query = get('ccSearch').value;
      let count = 0;
      catalog.forEach(item => {
        const visible = visibleCatalog.includes(item) && matches(item, query, category);
        page.querySelector(`[data-cc-card="${item.id}"]`).hidden = !visible;
        if (visible) count++;
      });
      get('ccResultCount').textContent = `${count} ${count === 1 ? 'section' : 'sections'}`;
      get('ccEmpty').hidden = count > 0;
      get('ccClear').hidden = !query;
      page.querySelectorAll('[data-cc-category]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.ccCategory === category));
        button.querySelector('.cc-category-count').textContent = visibleCatalog.filter(item=>matches(item, query, button.dataset.ccCategory)).length;
      });
      page.querySelectorAll('[data-cc-mac-link]').forEach(link=>{link.hidden = !macSupported;});
    }
    get('ccSearch').addEventListener('input', filter);
    function resetFilter() { get('ccSearch').value = ''; category = 'all'; filter(); get('ccSearch').focus(); }
    get('ccClear').onclick = () => { get('ccSearch').value = ''; filter(); get('ccSearch').focus(); };
    get('ccResetFilter').onclick = resetFilter;
    get('ccCategories').onclick = event => {
      const button = event.target.closest('[data-cc-category]');
      if (!button) return;
      category = button.dataset.ccCategory; filter();
    };
    page.querySelector('[href="#connection"]').onclick = event => { event.preventDefault(); openConnection(); };
    // The headset clock can differ from this client's. Age uses the client
    // receipt time, retained by the shared poll when it buffers an early sample.
    const isFresh = () => available && Number.isFinite(receivedAt) && Date.now() >= receivedAt && Date.now() - receivedAt <= 90000;
    const context = () => ({...getContext(), sample, fresh:isFresh() && sampleGeneration === getContext().generation});
    const controller = createController({api, getContext:context});
    function audioAvailability() {
      const enabled = context().online === true && context().fresh && !!sample?.volume && !controller.busy && !audioPending;
      ['ccVolume','ccApplyVolume','ccMute'].forEach(id=>{get(id).disabled = !enabled;});
    }
    function renderSample() {
      const values = metrics(isFresh() ? sample : null);
      page.querySelectorAll('[data-cc-metric]').forEach(el=>{el.textContent = values[el.dataset.ccMetric];});
      get('ccDeviceName').textContent = isFresh() ? sample.hostname || 'Steam Frame' : 'Steam Frame';
      const sampledAt = Number.isFinite(sample?.time) && sample.time > 0 && sample.time * 1000 <= 8640000000000000 ? new Date(sample.time * 1000) : null;
      get('ccSampleState').textContent = isFresh() ? `${sampledAt ? '采样于 ' + sampledAt.toLocaleTimeString() : '接收于 ' + new Date(receivedAt).toLocaleTimeString()} · 每 30 秒更新一次` :
        error || (receivedAt !== null ? '采样已过期 · 刷新以读取当前指标' : '连接 Frame 以读取实时指标');
      get('ccSampleState').classList.toggle('cc-offline', !isFresh());
      get('ccStatusLabel').textContent = isFresh() ? '已连接' : '无实时采样';
      get('ccStatusDot').classList.toggle('on', isFresh());
      get('ccStatusNote').textContent = isFresh() ? '通过 SSH 的状态为只读。可随意浏览；控制项仅在操作时执行。' :
        '设置指南和配套工具仍可使用。请先在设备上唤醒 Frame，然后重新连接。';
      get('ccBuild').textContent = isFresh() ? `SteamOS ${values.os} · build ${values.build}` : '系统信息不可用';
      const services = isFresh() ? sample.services || {} : {};
      get('ccServices').innerHTML = [['steamvr','SteamVR'],['desktop','桌面'],['lepton','Lepton · 端口 5555'],['rdp','RDP 进程']].map(([key,label])=>`<span><i class="dot ${services[key] === true ? 'on' : ''}"></i>${label}<b>${typeof services[key] === 'boolean' ? services[key] ? '运行中' : '未检测到' : '不可用'}</b></span>`).join('');
      if (sample?.volume && isFresh()) {
        const pct = Math.round(sample.volume.level * 100);
        if (!dirty && !controller.busy && !audioPending) get('ccVolume').value = Math.min(100,Math.max(0,pct));
        get('ccVolumeValue').textContent = `${get('ccVolume').value}%`;
        get('ccAudioReadback').textContent = `已上报 ${pct}% · ${sample.volume.muted ? 'muted' : 'unmuted'}`;
        get('ccMute').textContent = sample.volume.muted ? '取消静音' : '静音';
      } else {
        get('ccAudioReadback').textContent = isFresh() ? '音频读数不可用' : '等待音频读数';
        get('ccVolumeValue').textContent = '—';
      }
      audioAvailability();
    }
    async function changeAudio(change) {
      if (audioPending) return;
      const generation = getContext().generation;
      audioPending = true;
      get('ccAudioResult').textContent = '正在应用…';
      try {
        const pending = controller.audio(change);
        audioAvailability();
        const result = await pending;
        if (!result || generation !== getContext().generation) return;
        if ('level' in change) dirty = false;
        get('ccAudioResult').textContent = `${result.message || '音频已更新'}. 正在刷新读数…`;
        await refreshAudio();
        if (generation !== getContext().generation) return;
        if (!isFresh() || !sample?.volume) {
          get('ccAudioResult').textContent = '音频命令已发送；但无法确认当前输出。请重新连接并刷新。';
        } else {
          const reported = sample.volume;
          const matched = (!('level' in change) || Math.round(reported.level * 100) === Math.round(change.level * 100)) &&
            (!('muted' in change) || reported.muted === change.muted);
          get('ccAudioResult').textContent = `${matched ? '已确认' : '命令已发送 · 与上报输出不一致'}: ${Math.round(reported.level * 100)}%, ${reported.muted ? 'muted' : 'unmuted'}.${matched ? '' : ' 刷新以再次检查。'}`;
        }
      } catch (e) {
        if (generation !== getContext().generation) return;
        get('ccAudioResult').textContent = `无法应用音频： ${e.message}`;
      } finally { audioPending = false; renderSample(); }
    }
    // Force quit: an explicit check, then a confirmation for each game. A headset
    // switch drops the list, so a stale entry can't quit the other Frame's game.
    let runningApps = [];
    function renderRunning(message) {
      get('ccRunningList').innerHTML = runningApps.map(app=>`<li><span>${esc(app.name)}<small>${KINDS[app.kind] || '运行中'}</small></span><button class="small danger" type="button" data-cc-quit="${esc(app.appid)}"${controller.quitting ? ' disabled' : ''}>强制退出…</button></li>`).join('');
      if (message !== undefined) get('ccRunningResult').textContent = message;
    }
    async function checkRunning() {
      const generation = getContext().generation;
      get('ccRunningCheck').disabled = true;
      get('ccRunningResult').textContent = '正在检查…';
      try {
        const result = await controller.running();
        if (!result || generation !== getContext().generation) return;
        runningApps = Array.isArray(result.running) ? result.running : [];
        renderRunning(runningApps.length ? `${runningApps.length} 个正在运行。仅在游戏无响应时才强制退出。` : 'Frame 上没有正在运行的内容。');
      } catch (e) {
        if (generation !== getContext().generation) return;
        runningApps = []; renderRunning(`无法检查运行中的内容： ${e.message}`);
      } finally { get('ccRunningCheck').disabled = false; }
    }
    async function forceQuit(appid) {
      const app = runningApps.find(item=>String(item.appid) === String(appid));
      if (!app || controller.quitting) return;
      if (!confirm(`强制退出 ${app.name}？未保存的进度将丢失。`)) return;
      const generation = getContext().generation, pending = controller.forceQuit(app);
      renderRunning(`正在退出 ${app.name}…`);
      try {
        const result = await pending;
        if (!result || generation !== getContext().generation) return;
        runningApps = runningApps.filter(item=>item !== app);
        renderRunning(result.message || `已退出 ${app.name}`);
      } catch (e) {
        if (generation !== getContext().generation) return;
        renderRunning(`无法强制退出 ${app.name}: ${e.message}`);
      } finally { if (generation === getContext().generation) renderRunning(); }
    }
    get('ccRunningCheck').onclick = checkRunning;
    get('ccRunningList').onclick = event => {
      const button = event.target.closest('[data-cc-quit]');
      if (button) forceQuit(button.dataset.ccQuit);
    };
    get('ccVolume').oninput = () => { dirty = true; get('ccVolumeValue').textContent = `${get('ccVolume').value}%`; };
    get('ccApplyVolume').onclick = () => changeAudio({level:Number(get('ccVolume').value) / 100});
    get('ccMute').onclick = () => changeAudio({muted:!sample?.volume?.muted});
    get('ccRefresh').onclick = async () => {
      get('ccRefresh').disabled = true;
      try { await refresh(); } finally { get('ccRefresh').disabled = false; }
    };
    filter(); renderSample();
    // Expire UI readings even when the shared poll is paused in a hidden tab.
    const timer = setInterval(()=>{if (receivedAt !== null && available && !isFresh()) { available = false; renderSample(); }}, 5000);
    return {
      host(value) { macSupported = !value.mobile && value.os === 'macOS'; filter(); },
      status(value, receipt = Date.now(), read = {valid: true, error: null}) {
        // A redraw is not a successful read: preserve receipt age and any local
        // invalidation. Shared validity also carries failures across late mount.
        const replay = sample === value;
        receivedAt = replay ? (Number.isFinite(receivedAt) && Number.isFinite(receipt) ? Math.min(receivedAt, receipt) : receivedAt) : receipt;
        sample = value;
        if (!read.valid) {
          available = false; error = `无实时采样${read.error?.message ? ' · ' + read.error.message : ''}`;
        } else if (!replay) { available = true; error = ''; }
        sampleGeneration = getContext().generation; renderSample();
      },
      unavailable(reason) { if (reason?.stale) return; available = false; error = `无实时采样${reason?.message ? ' · ' + reason.message : ''}`; renderSample(); },
      connection(ok) { if (!ok) { available = false; renderSample(); } else audioAvailability(); },
      reset() { sample = null; receivedAt = null; available = false; error = ''; dirty = false; get('ccVolume').value = 0; get('ccAudioResult').textContent = ''; runningApps = []; renderRunning(''); renderSample(); },
      destroy() { clearInterval(timer); },
    };
  }
  return {catalog, categories, matches, metrics, createController, mount};
});
