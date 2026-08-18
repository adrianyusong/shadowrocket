# Shadowrocket 配置

个人的 Shadowrocket 规则、模块与脚本集合。

## 订阅

在 Shadowrocket 中：**配置 → 添加配置 → 填入下方地址**

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/default.conf
```

若 `raw.githubusercontent.com` 拉不动，可换 jsDelivr 镜像（有缓存延迟）：

```
https://cdn.jsdelivr.net/gh/adrianyusong/shadowrocket@main/config/default.conf
```

> 仓库为 Public。它不含任何节点凭据——`[Proxy]` 段留空，节点由 App 内订阅提供。

## 目录结构

| 目录 | 内容 |
|---|---|
| `config/` | 主配置文件（`.conf`） |
| `rule/` | 分流规则集（`.list`），已收编上游 |
| `module/` | 模块（`.module` / `.sgmodule`） |
| `script/` | 重写脚本（`.js`） |
| `tools/` | 维护脚本 |

### 规则集为什么收编进仓库

原先配置直接引用 54 个 blackmatrix7 的远程规则集。每次配置更新就是 54 次网络请求，
任何一个失败该类规则会**静默消失**，不报错。上游改动也会在你不知情时改变分流。

现在全部收编进 `rule/`，共 34 个文件、12.3 万条规则。

### 维护

| 文件 | 作用 |
|---|---|
| `tools/sources.txt` | **上游清单，脚本的唯一数据来源**。顺序即优先级 |
| `tools/exclude.txt` | 上游缺陷黑名单，同步时剔除 |
| `tools/sync-rules.py` | 拉取、去重、按策略合并 |
| `tools/check-config.py` | 静态校验，失败退出码非 0 |
| `.github/workflows/sync.yml` | 每周日自动同步，校验通过才提交 |

```bash
python tools/sync-rules.py && python tools/check-config.py
```

`sources.txt` 必须独立存在：规则集收编后配置里只剩指向本仓库的 URL，
脚本再也无法从配置反推上游。

`sync-rules.py` 会按 `sources.txt` 顺序拉取，然后**全局去重** —— 同一条规则只保留
首次出现的策略，与 Shadowrocket 自上而下的匹配语义一致。合并后文件之间不再有跨文件
冲突（原先有 680 条这类冲突，靠规则顺序隐式决定胜负）。同时把 QuantumultX 的
`HOST-SUFFIX` 归一化为原生 `DOMAIN-SUFFIX`。

`exclude.txt` 记录上游缺陷。收编后上游的错误也进了本仓库，手工删除会在下次同步时
被搬回，所以必须记在这里。当前豁免三条，每条都有实测依据：

- `DOMAIN-KEYWORD,jav` —— 误伤 `javascript.info`、`javadoc.io`
- `DOMAIN-SUFFIX,ms` —— `.ms` 是蒙特塞拉特国家域，非中国 gTLD
- `DOMAIN-SUFFIX,simility.com,reject-ads` —— PayPal 风控引擎，只从广告策略剔除

### 广告拦截

`reject-ads.list` 以 [anti-AD](https://anti-ad.net) 为主，10 万条。

引入原因：blackmatrix7 的 Advertising 系列按服务分类而非按广告网络，实测 19 个常见
广告域名只覆盖 4 个，换完整版 `Advertising`（781 条）也只到 2/15。anti-AD 同组覆盖
13/19，且对 LinkedIn、拼多多、QQ、GitHub、OpenAI、Google、Apple、支付宝、淘宝
九个正常服务 0 误伤。

**代价**：规则总量从 2.3 万涨到 12.3 万，iOS 端加载表现未在设备上验证。

`rule/*.list` 由脚本生成，**不要手改** —— 下次同步会覆盖。需要增补规则请写进
`config/default.conf` 的内联规则段（LinkedIn、拼多多、埋点拦截都在那里），
需要剔除上游规则请写进 `tools/exclude.txt`。

例外：`rule/ChinaDomains.list` 是手工维护的，不参与同步。

## 配置说明

`config/default.conf` 包含 43 个策略组、34 个规则集（全部托管于本仓库 `rule/`），
合计 12.3 万条规则。上游为 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)
与 [anti-AD](https://anti-ad.net)。

### 节点分组

自动测速类分组（`♻️ 自动选择`、`🔯 故障转移`、`🔮 负载均衡`）与 6 个地区分组
使用 `policy-regex-filter` 按**节点名关键词**匹配订阅节点。
`policy-regex-filter` 与逆序环视 `(?<!...)` 已在设备上确认生效。

地区正则里的英文缩写一律用 `(?<![A-Za-z])XX(?![A-Za-z])` 包裹。裸写 `US` 在
`(?i)` 下会匹配 R**us**sia、A**us**tralia、Br**us**sels、Pl**us**，把俄罗斯和
澳洲节点混进美国组 —— 而 `🤖 AI 服务` 以美国组为首选，落地混淆会直接触发风控。
同理不使用裸单字 `台` / `日` / `美`（会命中 烟台、台州、重置日、美食）。

某个地区分组为空，说明节点名不含该地区关键词，扩充对应正则即可。

### IP 稳定性

`🤖 AI 服务` 与流媒体分组的候选中**刻意不放** `🚀 节点选择` —— 它可间接指向
`🔮 负载均衡`，导致每个请求换一次出口，会被 OpenAI 判定异常、被 Netflix 判定
为代理。地区分组的测速间隔也调为 `interval = 600, tolerance = 200` 抑制抖动。

要彻底稳定，在 App 内把 `🤖 AI 服务` 直接指定为单个固定节点。

### 规则顺序

有几处是刻意安排的，改动时注意：

| 位置 | 原因 |
|---|---|
| QUIC 阻断在最前 | UDP 443 会绕过 TCP 侧规则匹配，也让流媒体解锁判定不稳 |
| 广告 / 隐私规则靠前 | 拦截优先于分流，否则被后面的域名规则抢先命中 |
| AI 服务在 Global / Microsoft / Google 之前 | 否则 OpenAI、Copilot 会被宽泛规则集吞掉 |
| GoogleFCM 在 Google 之前且走 DIRECT | FCM 走代理收不到推送 |
| AppleID 在 Apple 之前且走 DIRECT | 走代理易触发二次验证 |
| Bing 在 Microsoft 之前 | 否则被 Microsoft 规则集吞掉走直连 |
| 游戏 CDN 内联规则在 Steam / Blizzard 之前 | 见下 |
| BiliBiliIntl 在 BiliBili 之前 | 国际版需代理，国内版直连 |
| Speedtest 走直连 | 测本地真实带宽 |
| ChinaMax + GEOIP,CN 在 FINAL 之前 | 域名规则先行，IP 兜底 |

### 游戏下载为什么要单独拦

`🎮 游戏平台` 是代理优先（商店、社区、登录需要代理），但 `Steam.list` 与
`Blizzard.list` 内部混着下载 CDN 域名，整组走代理会把几十 GB 的游戏本体
拖进代理烧套餐。`SteamCN.list` 只覆盖国区 CDN，实测 `Steam.list` 里有 11 个
全球下载 CDN 域名不在其中（含 `steampipe.akamaized.net`、
`steamcdn-a.akamaihd.net`），Blizzard 另有 6 个（含主下载 CDN
`blzddist1-a.akamaihd.net`）。`Download.list` 排在更后面，救不了。

所以这 18 条域名以内联 `DOMAIN-SUFFIX` 规则显式拦到直连，位置在游戏规则集之前。

### 已知不确定项

`[General]` 中的 `private-ip-answer` 语义未在设备上验证。若国内域名解析异常，
优先注释该行排查。

引入 anti-AD 后规则总量从 2.3 万涨到 12.3 万，iOS 网络扩展的加载表现未在设备上
验证。若出现启动慢或内存告警，从 `tools/sources.txt` 移除 anti-AD 一行后重跑同步即可。

### MITM

`enable = true`。配置本身不含重写脚本，但 iRingo 一类模块需要 MITM，
且若这里写 false，每次拉取配置都会把 App 内的开关按回去，表现为模块间歇性失效。

`hostname` 留空是对的：模块用 `hostname = %APPEND% xxx` 追加自己的域名，
启用模块后自动并入全局列表。

证书需在 App 内生成并**在系统里信任**：

设置 → 证书 → 生成新的 CA 证书 → 安装 → 系统「通用 → 关于 → 证书信任设置」中启用

## 关于敏感信息

本仓库**不包含**任何真实的服务器地址、密码或机场订阅链接。

`[Proxy]` 段留空，节点通过 App 内订阅添加即可自动并入各策略组。若要保存本地节点，
复制一份命名为 `*.local.conf` 或 `*.private.conf` —— 这两类文件名已在
`.gitignore` 中排除，不会被提交。

MITM 的 `ca-passphrase` 与 `ca-p12` 是本机私钥，同样不要入库。
