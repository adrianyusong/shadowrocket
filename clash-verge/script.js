function main(config) {
  if (!config.proxies || config.proxies.length === 0) return config;

  // ============================================================
  // 安全鎖定（腳本最後合併，避免被 Verge 覆寫回預設值）
  // ============================================================
  config.secret = '<YOUR-CONTROLLER-SECRET>';   // 自行填入，或整行刪掉改在 Verge 的 Clash 設定裡設
  config['external-controller-cors'] = {
    'allow-private-network': true,
    'allow-origins': [
      'tauri://localhost',
      'http://tauri.localhost'
    ]
  };

  // 域名嗅探：改善純 IP 連線的分流準確度（不強行改寫目標，降低誤傷）
  config.sniffer = {
    enable: true,
    'force-dns-mapping': true,
    'parse-pure-ip': true,
    'override-destination': false,
    sniff: {
      HTTP: { ports: [80, '8080-8880'] },
      TLS: { ports: [443, 8443] },
      QUIC: { ports: [443, 8443] }
    },
    'skip-domain': [
      'Mijia Cloud',
      '+.push.apple.com'
    ]
  };

  // ============================================================
  // 效能優化
  // ============================================================
  config['tcp-concurrent'] = true;
  // unified-delay 不能在這裡設：Verge 會在腳本之後用自己的值覆寫。
  // 已改在 Verge「Clash 設定」(config.yaml) 開啟。
  config['geodata-mode'] = true;
  config['geo-auto-update'] = true;
  config['geo-update-interval'] = 24;
  // 預設來源是 GitHub raw，17 MB 的 geoip.dat 直連幾乎必定逾時。
  // 實測日誌：[GEO] can't download GeoIP database file: context deadline exceeded，
  // 4.2 MB 的 geosite.dat 抓得到、17 MB 的 geoip.dat 卡在 4 天前沒更新。
  // 改用 jsDelivr 鏡像（已完整下載比對：17,224,069 bytes，與官方一致、未被 CDN 截斷）。
  // 注意 ASN 的檔名是 GeoLite2-ASN.mmdb，asn.mmdb 會 404。
  config['geox-url'] = {
    geoip:   'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.dat',
    geosite: 'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geosite.dat',
    mmdb:    'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/country.mmdb',
    asn:     'https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/GeoLite2-ASN.mmdb'
  };
  // PROCESS-NAME 規則需要開啟（strict：有進程規則才查詢，效能較好）
  config['find-process-mode'] = 'strict';

  const typeMap = {};
  config.proxies.forEach(p => { typeMap[p.name] = p.type; });
  const allProxies = config.proxies.map(p => p.name);

  // 過濾無效信息節點
  const proxies = allProxies.filter(p =>
    !/剩[余餘]|到期|重[置设]|流量|套餐|[过過]期|Traffic|Expire|Reset|日[志誌]|官网|官方|地址|群/i.test(p)
  );

  // CF 中轉節點識別：這批 network: ws 的節點前置在 Cloudflare，出口是共享邊緣 IP。
  // 跑一般網頁沒問題，但支付風控（Stripe Radar / PayPal）對共享 IP 命中率極高，
  // 而且中轉多一跳，長交互容易中途斷 —— 支付與帳號綁定流程一律排除。
  const relaySet = new Set(config.proxies.filter(p => p.network === 'ws').map(p => p.name));
  const noRelay = list => list.filter(n => !relaySet.has(n));

  const isHy2  = name => typeMap[name] === 'hysteria2' || typeMap[name] === 'hysteria';
  const isVless = name => typeMap[name] === 'vless';
  const allHy2   = proxies.filter(isHy2);
  const allVless  = proxies.filter(isVless);

  // ============================================================
  // 1. 節點按地區分類
  // ============================================================
  // 拉丁字母代號一律加 \b：否則 US 會誤中 Russia/Belarus、GB 會誤中 Gbps 這類名稱
  const hk = proxies.filter(p => /港|\bHK\b|Hong\s*Kong/i.test(p));
  const tw = proxies.filter(p => /台[灣湾]|\bTW\b|Taiwan/i.test(p));
  const jp = proxies.filter(p => /日本|\bJP\b|Japan|[東东]京|大阪/i.test(p));
  const sg = proxies.filter(p => /新加坡|\bSG\b|\bSing\w*|[獅狮]城/i.test(p));
  const us = proxies.filter(p => /美[國国]|\bUS\b|\bUSA\b|United\s*States|America|洛杉[磯矶]|[舊旧]金山|[矽硅]谷/i.test(p));
  const kr = proxies.filter(p => /[韓韩][國国]?|\bKR\b|Korea|首[爾尔]/i.test(p));
  const gb = proxies.filter(p => /英[國国]|\bGB\b|\bUK\b|Britain|London|[伦倫]敦/i.test(p));

  // 🌍 動態 CF / 未知節點分組（反向排除法：只要沒被分進地區的，全歸入此類）
  const classifiedNodes = new Set([...hk, ...tw, ...jp, ...sg, ...us, ...kr, ...gb]);
  const cfWorkers = proxies.filter(p => !classifiedNodes.has(p));

  // ============================================================
  // 2. 自動測速與故障轉移分組
  // ============================================================
  const regionGroups = [];
  const safeAdd = (name, type, list, interval = 300) => {
    if (list.length > 0) {
      regionGroups.push({
        name, type: type, proxies: list,
        // 必須用 https：開了統一延遲後 mihomo 會送兩次 HEAD，
        // 機場若劫持了測速位址就會在第二次逾時，好節點被誤判成失敗而踢出候選
        url: 'https://www.gstatic.com/generate_204',
        interval: interval, tolerance: 100, lazy: true
      });
      return name;
    }
    return null;
  };

  // --- 自動最快 (url-test) ---
  const hkName = safeAdd('🇭🇰 香港 (自動最快)', 'url-test', hk);
  const twName = safeAdd('🇹🇼 台灣 (自動最快)', 'url-test', tw);
  const jpName = safeAdd('🇯🇵 日本 (自動最快)', 'url-test', jp);
  const sgName = safeAdd('🇸🇬 獅城 (自動最快)', 'url-test', sg);
  const usName = safeAdd('🇺🇸 美國 (自動最快)', 'url-test', us);
  const krName = safeAdd('🇰🇷 韓國 (自動最快)', 'url-test', kr);
  const gbName = safeAdd('🇬🇧 英國 (自動最快)', 'url-test', gb);
  const cfName = safeAdd('🌍 CF/未知 (自動最快)', 'url-test', cfWorkers);

  // --- 穩定備援 (fallback) - 專為 AI 設計 ---
  const usFallback = safeAdd('🇺🇸 美國 VLESS (穩定)', 'fallback', us.filter(isVless), 300);
  const jpFallback = safeAdd('🇯🇵 日本 VLESS (穩定)', 'fallback', jp.filter(isVless), 300);
  const sgFallback = safeAdd('🇸🇬 獅城 VLESS (穩定)', 'fallback', sg.filter(isVless), 300);
  const usHy2Fallback = safeAdd('🇺🇸 美國 Hy2 (穩定)', 'fallback', us.filter(isHy2), 300);

  // --- 協議細分 (url-test) ---
  const hkHy2Name  = safeAdd('🇭🇰 香港 Hy2 專線', 'url-test', hk.filter(isHy2));
  const hkVlessName = safeAdd('🇭🇰 香港 VLESS', 'url-test', hk.filter(isVless));
  const sgHy2Name  = safeAdd('🇸🇬 獅城 Hy2 專線', 'url-test', sg.filter(isHy2));
  const sgVlessName = safeAdd('🇸🇬 獅城 VLESS', 'url-test', sg.filter(isVless));
  const jpHy2Name  = safeAdd('🇯🇵 日本 Hy2 專線', 'url-test', jp.filter(isHy2));
  const jpVlessName = safeAdd('🇯🇵 日本 VLESS', 'url-test', jp.filter(isVless));
  const usHy2Name  = safeAdd('🇺🇸 美國 Hy2 專線', 'url-test', us.filter(isHy2));
  const usVlessName = safeAdd('🇺🇸 美國 VLESS', 'url-test', us.filter(isVless));
  const krHy2Name  = safeAdd('🇰🇷 韓國 Hy2 專線', 'url-test', kr.filter(isHy2));
  const twHy2Name  = safeAdd('🇹🇼 台灣 Hy2 專線', 'url-test', tw.filter(isHy2));
  const gbHy2Name  = safeAdd('🇬🇧 英國 Hy2 專線', 'url-test', gb.filter(isHy2));

  // --- 支付／風控專用：排除 CF 中轉，只留直連節點 ---
  const usDirectName = safeAdd('🇺🇸 美國 直連 (無中轉)', 'url-test', noRelay(us));
  const jpDirectName = safeAdd('🇯🇵 日本 直連 (無中轉)', 'url-test', noRelay(jp));
  const sgDirectName = safeAdd('🇸🇬 獅城 直連 (無中轉)', 'url-test', noRelay(sg));

  // --- 全協議匯總 ---
  const allHy2Name  = safeAdd('🚄 全部 Hy2 專線', 'url-test', allHy2);
  const allVlessName = safeAdd('🌐 全部 VLESS', 'url-test', allVless);

  // ============================================================
  // 3. 組裝選擇列表
  // ============================================================
  const regionNames = [hkName, twName, jpName, sgName, usName, krName, gbName, cfName].filter(Boolean);
  if (regionNames.length === 0) regionNames.push(...proxies);
  const protocolNames = [allHy2Name, allVlessName].filter(Boolean);

  // 支付／帳號綁定類流程專用清單。這類長交互對連線穩定度敏感，
  // Hy2 走 UDP 在部分網路會被 QoS 掉；VLESS over TCP 相容性較好。
  // 注意：只有「🇭🇰 香港 VLESS」全部是 REALITY 直連，其餘地區的 VLESS
  // 組裡混有 Cloudflare 中轉的 ws 節點（server: cfyes.*），中轉節點跑
  // 支付流程容易中途斷，選節點時優先香港。
  const vlessNames = [hkVlessName, sgVlessName, jpVlessName, usVlessName,
                      usFallback, sgFallback, jpFallback, allVlessName].filter(Boolean);

  // 支付／帳號綁定優先清單：全部是無中轉的直連節點，排在選單最前面
  const payNames = [hkVlessName, hkHy2Name, usDirectName, sgDirectName, jpDirectName].filter(Boolean);

  // ── 另一個訂閱（edgetunnel）以 proxy-provider 形式併入 ──────────────
  // 限制說明：provider 的節點在腳本執行時還不存在（mihomo 是執行期才去抓的），
  // 所以上面那整套地區／CF 中轉／協議分類對它們一律無效，只能整包暴露成兩個組。
  // 影響很小：這批是 Cloudflare Workers 節點，就算跑過分類也會全部落進 🌍 CF/未知。
  // 刻意不放進 💰 PayPal / 🤖 AI 服務 / 💻 AI 編程 —— 那幾組要的是可控且固定的出口。
  const EDGE_PROVIDER = 'edgetunnel';
  const edgeSelName  = '🌩️ EdgeTunnel';
  const edgeAutoName = '🌩️ EdgeTunnel (自動)';
  const edgeNames = [edgeAutoName, edgeSelName];

  // 代理組清單去重：原本 usName…jpName 和 regionNames 會重複列出同一個組
  const uniq = list => [...new Set(list.filter(Boolean))];

  // AI 服務：機場解鎖在 JP，優先日本穩定節點；另提供美/台自動最快
  // payNames 緊接在預設值之後：訂閱／付款時要能快速把 AI 服務切到跟 💰 PayPal
  // 同一個無中轉節點，避免商家頁面與支付 API 來自不同出口 IP
  const aiProxies = [jpFallback, ...payNames, jpVlessName, jpHy2Name, jpName, usName, twName, usFallback, usVlessName, sgFallback, allVlessName, allHy2Name, '🚀 節點選擇'].filter(Boolean);

  // 全局最快：只選穩定節點。
  // 舊條件含「高速|BGP」，本機場幾乎每個節點名都有 → 58 個篩出 46 個，等於沒篩。
  // 改為只認專線 / 住宅 IP / IPLC / IEPL，篩出 14 個真正穩的。
  let fastProxies = proxies.filter(p => /专线|專線|住宅|IPLC|IEPL|Premium/i.test(p));
  if (fastProxies.length === 0) fastProxies = proxies.filter(p => /高速|BGP|443/i.test(p));

  // ============================================================
  // 4. 雲端規則集
  // ============================================================
  // Domain 版匹配更快；無 Domain 檔時回退 classical
  // proxy：走代理抓取。直連時 jsdelivr 一被污染，20 個 provider 會集體靜默失效，
  //        所有 RULE-SET 消失、流量整批掉到 GEOSITE / MATCH，且不會有明顯報錯。
  const rp = (name, folder, file, behavior = 'classical', interval = 86400) => ({
    type: 'http',
    behavior,
    url: `https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/${folder}/${file}.yaml`,
    path: `./ruleset/${name.toLowerCase()}.yaml`,
    interval,
    proxy: '🚀 節點選擇'
  });
  // 走代理後更新要算流量：AdBlock 7.1 MB、ChinaMax 2.3 MB，且內容變動慢 → 改 7 天
  const WEEK = 86400 * 7;

  config['proxy-providers'] = {
    [EDGE_PROVIDER]: {
      type: 'http',
      url: 'https://<YOUR-SUB-HOST>/sub?token=<YOUR-TOKEN>',
      interval: 3600,
      path: './providers/edgetunnel.yaml',
      // 抓取節點的選擇有兩個約束：
      // 1) 不能指向含 EdgeTunnel 的組（🚀 節點選擇），否則「要抓節點得先有節點」循環依賴
      // 2) 實測 🇭🇰香港专线02（住宅 IP）連 edge-doo.pages.dev 會 25 秒逾時 ——
      //    Cloudflare Pages 對住宅代理 IP 的封鎖很常見；同樣是 Hy2 的 🇺🇸美国05 則 0.9 秒 200。
      // usDirectName 是無中轉的美國直連組（含美国05），兩個約束都滿足
      proxy: usDirectName || usName || '⚡ 全局最快大亂鬥',
      // health-check 必須開，否則 provider 節點在 url-test 組裡沒有延遲資料可比
      // 用 https：mihomo 會警告 HTTP 測速位址容易被機場劫持，開了統一延遲後更明顯
      'health-check': { enable: true, interval: 600, url: 'https://www.gstatic.com/generate_204' }
    }
  };

  config['rule-providers'] = {
    // 🛡️ 安全
    AdBlock: rp('AdBlock', 'Advertising', 'Advertising_Domain', 'domain', WEEK),
    Hijacking: rp('Hijacking', 'Hijacking', 'Hijacking'),
    // HTTPDNS：國內 App 普遍用 HTTP 直接向自家伺服器查域名，繞過系統 DNS。
    // 這會架空整套分流 —— App 拿到真實 IP 後直連，fake-ip、域名規則、rule-provider
    // 全部形同虛設，日誌裡也只剩裸 IP。擋掉之後 App 會退回正常 DNS，規則才管得到。
    // 來源不是 blackmatrix7，所以不能用 rp()；改用 testingcf 鏡像（境內可用性較好）
    HTTPDNS: {
      type: 'http',
      behavior: 'classical',
      url: 'https://testingcf.jsdelivr.net/gh/dler-io/Rules@main/Clash/Provider/HTTPDNS.yaml',
      path: './ruleset/httpdns.yaml',
      interval: WEEK,
      proxy: '🚀 節點選擇'
    },
    // Privacy.yaml 上游只有 20 條（9 個 keyword + 11 個 IP-CIDR），
    // 檔頭宣稱的 39,897 條 DOMAIN-SUFFIX 全在 Privacy_Domain.yaml，必須另外引用
    Privacy: rp('Privacy', 'Privacy', 'Privacy'),
    PrivacyDomain: rp('PrivacyDomain', 'Privacy', 'Privacy_Domain', 'domain'),

    AliPay: rp('AliPay', 'AliPay', 'AliPay'),
    ChinaMax: rp('ChinaMax', 'ChinaMax', 'ChinaMax_Domain', 'domain', WEEK),

    GlobalMedia: rp('GlobalMedia', 'GlobalMedia', 'GlobalMedia_Domain', 'domain'),
    YouTube: rp('YouTube', 'YouTube', 'YouTube'),
    Steam: rp('Steam', 'Steam', 'Steam'),
    Telegram: rp('Telegram', 'Telegram', 'Telegram'),

    Bahamut: rp('Bahamut', 'Bahamut', 'Bahamut'),
    Netflix: rp('Netflix', 'Netflix', 'Netflix'),
    Disney: rp('Disney', 'Disney', 'Disney'),
    Spotify: rp('Spotify', 'Spotify', 'Spotify'),
    PrimeVideo: rp('PrimeVideo', 'AmazonPrimeVideo', 'AmazonPrimeVideo'),

    Apple: rp('Apple', 'Apple', 'Apple_Domain', 'domain'),
    Microsoft: rp('Microsoft', 'Microsoft', 'Microsoft'),

    OpenAI: rp('OpenAI', 'OpenAI', 'OpenAI'),
    Gemini: rp('Gemini', 'Gemini', 'Gemini'),
    Claude: rp('Claude', 'Claude', 'Claude'),
    Copilot: rp('Copilot', 'Copilot', 'Copilot'),
    Google: rp('Google', 'Google', 'Google'),
    GitHub: rp('GitHub', 'GitHub', 'GitHub')
  };

  // ============================================================
  // 5. 代理組設定
  // ============================================================
  config['proxy-groups'] = [
    { name: '🚀 節點選擇', type: 'select', proxies: uniq(['⚡ 全局最快大亂鬥', usDirectName, sgDirectName, ...protocolNames, ...regionNames, ...edgeNames]) },
    { name: '⚡ 全局最快大亂鬥', type: 'url-test', proxies: fastProxies.length > 0 ? fastProxies : proxies, url: 'https://www.gstatic.com/generate_204', interval: 600, tolerance: 100, lazy: true },

    ...regionGroups,

    { name: edgeAutoName, type: 'url-test', use: [EDGE_PROVIDER], url: 'https://www.gstatic.com/generate_204', interval: 300, tolerance: 100, lazy: true },
    // select + use：這是目前唯一可以手選單一節點的組
    { name: edgeSelName, type: 'select', use: [EDGE_PROVIDER] },

    { name: '🤖 AI 服務', type: 'select', proxies: uniq(aiProxies) },
    { name: '💻 AI 編程', type: 'select', proxies: uniq([jpFallback, usDirectName, sgDirectName, jpVlessName, jpName, usFallback, usVlessName, sgFallback, allVlessName, allHy2Name, '🚀 節點選擇', ...regionNames]) },
    { name: '🐙 GitHub', type: 'select', proxies: [usName, jpName, sgName, usVlessName, jpVlessName, '🚀 節點選擇', ...regionNames].filter(Boolean) },
    { name: '🎥 串流媒體', type: 'select', proxies: uniq(['🚀 節點選擇', ...protocolNames, ...regionNames, ...edgeNames]) },
    { name: '🎌 巴哈姆特', type: 'select', proxies: [twName, twHy2Name, '🚀 節點選擇'].filter(Boolean) },
    // 預設沿用目前實際落點（美國直連），避免改動後行為突變；
    // 想要低延遲就往下選日本／獅城，但注意倍率：美國 0.01x~0.1x，日本專線是 1x
    { name: '📹 YouTube', type: 'select', proxies: uniq([usDirectName, jpDirectName, sgDirectName, jpName, sgName, hkName, usName, ...protocolNames, '🚀 節點選擇', ...regionNames]) },
    { name: '🎬 Netflix', type: 'select', proxies: [usName, usVlessName, usHy2Name, usFallback, '🚀 節點選擇'].filter(Boolean) },
    { name: '🏰 Disney+', type: 'select', proxies: [sgName, sgVlessName, sgHy2Name, sgFallback, '🚀 節點選擇'].filter(Boolean) },
    { name: '🎵 Spotify', type: 'select', proxies: [jpName, jpVlessName, jpHy2Name, jpFallback, '🚀 節點選擇'].filter(Boolean) },
    { name: '📺 Prime Video', type: 'select', proxies: [jpName, jpVlessName, jpHy2Name, jpFallback, '🚀 節點選擇'].filter(Boolean) },
    { name: '🔍 Google', type: 'select', proxies: [usName, jpName, sgName, usVlessName, jpVlessName, usHy2Name, jpHy2Name, '🚀 節點選擇', ...regionNames].filter(Boolean) },
    { name: '📲 Telegram', type: 'select', proxies: uniq(['🚀 節點選擇', ...protocolNames, ...regionNames, ...edgeNames]) },
    { name: '🎮 遊戲平台', type: 'select', proxies: uniq(['DIRECT', '🚀 節點選擇', ...protocolNames, ...regionNames, ...edgeNames]) },
    // 預設直連；國外片源播不動時可在代理組改走節點
    { name: '📺 本地播放器', type: 'select', proxies: ['DIRECT', '🚀 節點選擇', ...regionNames] },
    // 預設仍是直連（國內場景付款走直連最穩），但跨境綁定（如 Apple 帳號綁 PayPal）
    // 必須改選節點，且要跟 🍎 蘋果服務 用同一地區——兩端出口 IP 不一致會觸發 PayPal 風控
    { name: '💰 PayPal', type: 'select', proxies: uniq(['DIRECT', ...payNames, ...vlessNames, '🚀 節點選擇', usName, hkName, sgName, twName, jpName, ...regionNames]) },
    { name: '🍎 蘋果服務', type: 'select', proxies: uniq(['🚀 節點選擇', 'DIRECT', ...payNames, ...vlessNames, ...protocolNames, ...regionNames]) },
    { name: '🍎 蘋果下載', type: 'select', proxies: ['DIRECT', '🚀 節點選擇'] },
    { name: 'Ⓜ️ 微軟服務', type: 'select', proxies: ['🚀 節點選擇', 'DIRECT'] },
    { name: '🛡️ 廣告攔截', type: 'select', proxies: ['REJECT', 'DIRECT'] },
    // 掛上 Privacy_Domain 後規則量從 20 條變成近 4 萬條，誤殺面大幅擴大，
    // 預設先 DIRECT（只觀察不攔），確認沒踩到常用服務再手動切 REJECT
    { name: '🔏 隱私追蹤', type: 'select', proxies: ['DIRECT', 'REJECT', '🚀 節點選擇'] },
    { name: '🐟 漏網之魚', type: 'select', proxies: ['🚀 節點選擇', 'DIRECT'] }
  ];

  // ============================================================
  // 6. 分流規則
  // ============================================================
  config['rules'] = [
    // 🏠 本地設備與路由器直連保障
    // 私有網段提到最前面：純 IP 存取 NAS / 路由器 / 印表機不必再穿過整串規則
    'GEOIP,private,DIRECT,no-resolve',
    'DOMAIN-SUFFIX,local,DIRECT',
    'DOMAIN-SUFFIX,localhost,DIRECT',
    'DOMAIN,router.asus.com,DIRECT',
    'DOMAIN,miwifi.com,DIRECT',
    'DOMAIN,tplogin.cn,DIRECT',
    'DOMAIN,tendawifi.com,DIRECT',
    'DOMAIN,wifi.vivo.com,DIRECT',
    'DOMAIN,p.to,DIRECT',

    // WPAD 自動探測外洩：Windows 會去查公網註冊的 wpad.net，
    // 誰持有該網域誰就能對本機派送代理設定（典型中間人面），一律拒絕。
    // fake-ip-filter 的 '+.wpad' 只管 DNS 不管路由，這條才是實際攔截
    'DOMAIN-SUFFIX,wpad.net,REJECT',

    // 📋 IPTV 播放列表來源 → 走代理。必須排在下面的 PROCESS-NAME 之前，
    // 否則會被進程規則拖進 📺 本地播放器（該組直連，抓不到這些來源）。
    // 分工：列表檔案走代理，m3u 裡的實際串流（cdn.qd.je、*.163189.xyz 等亞洲源）
    // 仍由 PROCESS-NAME 接管走直連，延遲最低也不吃機場流量。
    // 用 DOMAIN 精確匹配而非後綴：dpdns.org 是公共動態 DNS，
    // 同層其他主機名可能是串流節點，不該一起拉進代理
    'DOMAIN,<YOUR-IPTV-PLAYLIST-HOST>,🚀 節點選擇',
    'DOMAIN-SUFFIX,githubusercontent.com,🐙 GitHub',
    'DOMAIN,bit.ly,🚀 節點選擇',
    // 開源 IPTV 列表源。ChinaMax 按 .cn 後綴判成直連，但主機實際在海外，
    // 直連必逾時（實測 108.160.165.212 i/o timeout）。同 c.pki.goog 那類誤判
    'DOMAIN-SUFFIX,fanmingming.cn,🚀 節點選擇',

    // 📺 PotPlayer / VLC / IPTV Player Zero 進程直連（需 find-process-mode；TUN 下最有效）
    'PROCESS-NAME,PotPlayerMini64.exe,📺 本地播放器',
    'PROCESS-NAME,PotPlayerMini.exe,📺 本地播放器',
    'PROCESS-NAME,PotPlayer64.exe,📺 本地播放器',
    'PROCESS-NAME,PotPlayer.exe,📺 本地播放器',
    'PROCESS-NAME,vlc.exe,📺 本地播放器',
    // IPTV Player Zero（Microsoft Store / FullTrust）：主程式 + 內嵌 mpv 播流
    'PROCESS-NAME,iptv-player-zero.exe,📺 本地播放器',
    'PROCESS-NAME,mpv.exe,📺 本地播放器',
    // Fred TV（原 Open TV）：主程式；播流仍走內嵌 mpv.exe
    'PROCESS-NAME,open_tv.exe,📺 本地播放器',

    // AdBlock 白名單：放行 Sentry（Cursor 等錯誤回報，避免被廣告規則誤攔）
    'DOMAIN-SUFFIX,sentry.io,🚀 節點選擇',
    // Google FCM 推播通道：Advertising_Domain 收了 +.mtalk.google.com，
    // 被 REJECT 會讓 ChatGPT 桌面版等走 FCM 的應用收不到推播
    'DOMAIN-SUFFIX,mtalk.google.com,🔍 Google',
    'DOMAIN-SUFFIX,gcm-http.googleapis.com,🔍 Google',
    'DOMAIN-SUFFIX,fcm.googleapis.com,🔍 Google',
    // 瀏覽器釣魚／惡意網站防護：被 Advertising_Domain 收走等於靜默關閉安全防護
    'DOMAIN-SUFFIX,safebrowsing.googleapis.com,🔍 Google',
    // 憑證吊銷檢查（CRL / OCSP）：ChinaMax 把 c.pki.goog、ocsp.pki.goog 列為直連，
    // 但本機直連不通，CryptSvc 會每 5 秒重試到逾時，拖慢所有 TLS 握手。
    // 必須排在 RULE-SET,ChinaMax 之前
    'DOMAIN-SUFFIX,pki.goog,🔍 Google',

    // 💰 PayPal（必須在 AdBlock 之前：c.paypal.com 等會被廣告規則誤攔）
    'DOMAIN-SUFFIX,paypal.com,💰 PayPal',
    'DOMAIN-SUFFIX,paypalobjects.com,💰 PayPal',
    'DOMAIN-SUFFIX,paypal.cn,💰 PayPal',
    'DOMAIN-SUFFIX,braintreegateway.com,💰 PayPal',
    'DOMAIN-SUFFIX,braintree-api.com,💰 PayPal',

    // 💳 Stripe：必須跟 PayPal 共用出口。r.stripe.com 是 Radar 的風險訊號端點，
    // 若它和 api.stripe.com 來自不同 IP，會直接判定訊號來源不一致而拒付。
    // 另外 stripecdn.com / stripe.network 是獨立註冊網域，stripe.com 的後綴蓋不到。
    // 必須排在 RULE-SET,OpenAI 之前：openai.yaml 自己收了 DOMAIN-SUFFIX,stripe.com
    'DOMAIN-SUFFIX,stripe.com,💰 PayPal',
    'DOMAIN-SUFFIX,stripecdn.com,💰 PayPal',
    'DOMAIN-SUFFIX,stripe.network,💰 PayPal',

    // DeepL 擴充的 background.js 每次 initialize 失敗就立刻重試（實測 2.5 秒內 3 次），
    // 被 AdBlock 的 '+.statsigapi.net' REJECT 後形成無限迴圈 ——
    // 每分鐘 30-60 筆，佔掉整份日誌的 51%，把有用的記錄擠出保留視窗。
    // 用 REJECT-DROP 靜默丟棄而非主動拒絕：攔截效果相同，但客戶端要等自己逾時
    // 才會重試，頻率降到個位數。必須排在 RULE-SET,AdBlock 之前才會生效。
    'DOMAIN-SUFFIX,statsigapi.net,REJECT-DROP',

    // 🛡️ 廣告與劫持攔截
    'RULE-SET,AdBlock,🛡️ 廣告攔截',
    'RULE-SET,Hijacking,🛡️ 廣告攔截',
    // 走 🛡️ 廣告攔截 而不是直接 REJECT：少數 App 在 HTTPDNS 不通時不會優雅退回，
    // 掛在可切換的組上，真出事時改成 DIRECT 就能立刻恢復
    'RULE-SET,HTTPDNS,🛡️ 廣告攔截',

    // 💰 金融支付強制直連（在 Privacy 之前，避免支付 SDK 被誤攔）
    'RULE-SET,AliPay,DIRECT',
    'DOMAIN-SUFFIX,alipay.com,DIRECT',
    'DOMAIN-SUFFIX,alipayobjects.com,DIRECT',
    'DOMAIN-SUFFIX,alipaydns.com,DIRECT',
    'DOMAIN-SUFFIX,alipay.cn,DIRECT',
    'DOMAIN-SUFFIX,mybank.cn,DIRECT',
    'DOMAIN-SUFFIX,95599.cn,DIRECT',
    'DOMAIN-SUFFIX,abchina.com,DIRECT',
    'DOMAIN-SUFFIX,icbc.com.cn,DIRECT',
    'DOMAIN-SUFFIX,ccb.com,DIRECT',
    'DOMAIN-SUFFIX,boc.cn,DIRECT',
    'DOMAIN-SUFFIX,bankcomm.com,DIRECT',
    'DOMAIN-SUFFIX,psbc.com,DIRECT',
    'DOMAIN-SUFFIX,cmbchina.com,DIRECT',
    'DOMAIN-SUFFIX,cib.com.cn,DIRECT',
    'DOMAIN-SUFFIX,spdb.com.cn,DIRECT',
    'DOMAIN-SUFFIX,bankofbeijing.com.cn,DIRECT',
    'DOMAIN-SUFFIX,cgbchina.com.cn,DIRECT',
    'DOMAIN-SUFFIX,pingan.com,DIRECT',
    'DOMAIN-SUFFIX,webank.com,DIRECT',
    'DOMAIN-SUFFIX,unionpay.com,DIRECT',
    'DOMAIN-SUFFIX,95516.com,DIRECT',
    'DOMAIN-SUFFIX,tenpay.com,DIRECT',
    'DOMAIN-SUFFIX,wx.qq.com,DIRECT',
    'DOMAIN-SUFFIX,weixin.qq.com,DIRECT',
    'DOMAIN-SUFFIX,pay.weixin.qq.com,DIRECT',

    // 🏛️ 政務 / 民生敏感域強制直連
    'DOMAIN-SUFFIX,gov.cn,DIRECT',
    'DOMAIN-SUFFIX,12306.cn,DIRECT',
    'DOMAIN-SUFFIX,12306.com,DIRECT',
    'DOMAIN-SUFFIX,rails.com.cn,DIRECT',
    'DOMAIN-SUFFIX,chinatax.gov.cn,DIRECT',
    'DOMAIN-SUFFIX,nhsa.gov.cn,DIRECT',
    'DOMAIN-SUFFIX,gjzwfw.gov.cn,DIRECT',
    'DOMAIN-SUFFIX,www.gov.cn,DIRECT',
    'DOMAIN-SUFFIX,12333.gov.cn,DIRECT',
    'DOMAIN-SUFFIX,beijing.gov.cn,DIRECT',

    // 🔏 隱私追蹤（預設 DIRECT 只觀察；確認無誤傷後在代理組改 REJECT）
    'RULE-SET,Privacy,🔏 隱私追蹤',
    'RULE-SET,PrivacyDomain,🔏 隱私追蹤',

    'RULE-SET,ChinaMax,DIRECT',

    // 💻 AI 編程工具 (Antigravity / Cursor / Claude Code / VS Code)
    // -- Antigravity / Google AI（必須在 Google 規則集之前，避免被 🔍 Google 搶走）
    'DOMAIN-SUFFIX,antigravity.google.com,💻 AI 編程',
    'DOMAIN-SUFFIX,antigravity-pa.googleapis.com,💻 AI 編程',
    'DOMAIN-SUFFIX,aistudio.google.com,💻 AI 編程',
    'DOMAIN-SUFFIX,alkalimakersuite-pa.googleapis.com,💻 AI 編程',
    'DOMAIN-SUFFIX,generativelanguage.googleapis.com,💻 AI 編程',
    'DOMAIN-SUFFIX,daily-cloudcode-pa.googleapis.com,💻 AI 編程',
    'DOMAIN-SUFFIX,cloudcode-pa.googleapis.com,💻 AI 編程',
    'DOMAIN-SUFFIX,aiplatform.googleapis.com,💻 AI 編程',
    'DOMAIN-SUFFIX,notebooklm.google,💻 AI 編程',
    'DOMAIN-SUFFIX,notebooklm.google.com,💻 AI 編程',
    'DOMAIN-SUFFIX,gemini.google.com,💻 AI 編程',
    'DOMAIN-SUFFIX,bard.google.com,💻 AI 編程',
    'DOMAIN-SUFFIX,deepmind.google,💻 AI 編程',
    'DOMAIN-SUFFIX,deepmind.com,💻 AI 編程',
    // -- Cursor
    'DOMAIN-SUFFIX,cursor.sh,💻 AI 編程',
    'DOMAIN-SUFFIX,cursor.com,💻 AI 編程',
    'DOMAIN-SUFFIX,cursorapi.com,💻 AI 編程',
    'DOMAIN-SUFFIX,cursor.so,💻 AI 編程',
    'DOMAIN-SUFFIX,cursorai.com,💻 AI 編程',
    // -- Claude Code (Anthropic)
    'DOMAIN-SUFFIX,anthropic.com,💻 AI 編程',
    'DOMAIN-SUFFIX,claude.ai,💻 AI 編程',
    'DOMAIN-SUFFIX,claudeusercontent.com,💻 AI 編程',
    // Claude Code / Cursor 的遙測端點。用 keyword 是因為 browser-intake-us5-datadoghq.com
    // 是獨立註冊網域，不是 datadoghq.com 的子網域，DOMAIN-SUFFIX 蓋不到
    'DOMAIN-KEYWORD,datadoghq,💻 AI 編程',

    // -- Antigravity IDE：主程式 + 內嵌 language_server 的全部流量收攏到同一出口。
    // 實測日誌顯示它同時打 antigravity-unleash.goog（掉到 GeoSite 兜底）、
    // oauth2/www/play.googleapis.com（Google 規則集）、daily-cloudcode-pa（AI 編程），
    // 三個組各走各的；OAuth 憑證與 API 呼叫來自不同出口 IP 會被 Google 判定異常而反覆要求重新登入。
    // 用進程規則比追域名可靠：Google 換端點（實際端點和官網域名對不上）也不用改規則。
    // 註：language_server.exe 是 Codeium 系列通用檔名，Windsurf 也會一起被歸進來
    'PROCESS-NAME,Antigravity.exe,💻 AI 編程',
    'PROCESS-NAME,language_server.exe,💻 AI 編程',
    // 進程識別失效時的備援（keyword 夠specific，也蓋得到 Cloud Run 上的自動更新端點）
    'DOMAIN-KEYWORD,antigravity,💻 AI 編程',
    // -- VS Code / GitHub Copilot
    'DOMAIN-SUFFIX,vscode.dev,💻 AI 編程',
    'DOMAIN-SUFFIX,vscode-cdn.net,💻 AI 編程',
    'DOMAIN-SUFFIX,vscode-unpkg.net,💻 AI 編程',
    'DOMAIN-SUFFIX,vsassets.io,💻 AI 編程',
    'DOMAIN-SUFFIX,gallerycdn.vsassets.io,💻 AI 編程',
    'DOMAIN-SUFFIX,update.code.visualstudio.com,💻 AI 編程',
    'DOMAIN-SUFFIX,az764295.vo.msecnd.net,💻 AI 編程',
    'DOMAIN-SUFFIX,copilot.github.com,💻 AI 編程',
    'DOMAIN-SUFFIX,githubcopilot.com,💻 AI 編程',
    'DOMAIN-SUFFIX,default.exp-tas.com,💻 AI 編程',

    // 通用基礎設施：OpenAI 規則集含 auth0.com / algolia.net 這類泛用後綴，
    // 先放行，避免一堆網站的登入流程與文件站搜尋被綁死在 AI 節點上
    'DOMAIN-SUFFIX,auth0.com,🚀 節點選擇',
    'DOMAIN-SUFFIX,algolia.net,🚀 節點選擇',

    // OpenAI 移到 AI 編程規則之後：openai.yaml 收了 api.statsig.com、
    // browser-intake-datadoghq.com，放前面會把編程工具的遙測搶進 🤖 AI 服務
    'RULE-SET,OpenAI,🤖 AI 服務',
    // 以下三組主要域名已被上面的 AI 編程規則接走，這裡只補殘餘：
    // Gemini → colab / ai.google.dev 等；Claude → 僅剩 cdn.usefathom.com
    'RULE-SET,Gemini,🤖 AI 服務',
    'RULE-SET,Claude,🤖 AI 服務',
    'RULE-SET,Copilot,🤖 AI 服務',

    // GitHub 在 Google 之前，避免部分資源被寬鬆規則誤傷
    'RULE-SET,GitHub,🐙 GitHub',
    'DOMAIN-SUFFIX,github.com,🐙 GitHub',
    'DOMAIN-SUFFIX,githubusercontent.com,🐙 GitHub',
    'DOMAIN-SUFFIX,githubassets.com,🐙 GitHub',
    'DOMAIN-SUFFIX,ghcr.io,🐙 GitHub',
    'DOMAIN-SUFFIX,npm.pkg.github.com,🐙 GitHub',

    // 📹 YouTube 必須排在 RULE-SET,Google 之前。
    // google.yaml 裡有 DOMAIN-KEYWORD,google，會把 googlevideo.com（影片流本體、
    // 也是最大宗流量）撈進 🔍 Google，而其餘 YouTube 域名落在 GlobalMedia →
    // 同一個服務被拆成兩個組，調整串流地區時影片流根本不跟著走。
    'RULE-SET,YouTube,📹 YouTube',
    'RULE-SET,Google,🔍 Google',
    'RULE-SET,Telegram,📲 Telegram',
    'RULE-SET,Steam,🎮 遊戲平台',

    // 🎬 串流精細分流（各服務 → 對應解鎖地區）
    'DOMAIN-SUFFIX,90zhibo.net,🎥 串流媒體',
    'DOMAIN-SUFFIX,90zhibo.com,🎥 串流媒體',
    'RULE-SET,Bahamut,🎌 巴哈姆特',
    'RULE-SET,Netflix,🎬 Netflix',
    'RULE-SET,Disney,🏰 Disney+',
    'RULE-SET,Spotify,🎵 Spotify',
    'RULE-SET,PrimeVideo,📺 Prime Video',
    'RULE-SET,GlobalMedia,🎥 串流媒體',

    // 🍎 蘋果服務精細分流
    'DOMAIN,iosapps.itunes.apple.com,🍎 蘋果下載',
    'DOMAIN,osxapps.itunes.apple.com,🍎 蘋果下載',
    'DOMAIN,updates-http.cdn-apple.com,🍎 蘋果下載',
    'DOMAIN-SUFFIX,ls.apple.com,🍎 蘋果下載',
    'DOMAIN-SUFFIX,swcdn.apple.com,🍎 蘋果下載',
    'DOMAIN-SUFFIX,appldnld.apple.com,🍎 蘋果下載',
    'RULE-SET,Apple,🍎 蘋果服務',

    'RULE-SET,Microsoft,Ⓜ️ 微軟服務',

    'DOMAIN-SUFFIX,qidian.com,DIRECT',
    'DOMAIN-SUFFIX,yuewen.com,DIRECT',

    'GEOSITE,geolocation-!cn,🚀 節點選擇',
    'GEOSITE,cn,DIRECT',
    // GEOIP,lan 已提到規則最前面（GEOIP,private，兩者同義）
    'GEOIP,cn,DIRECT',
    'MATCH,🐟 漏網之魚'
  ];

  return config;
}
