# Shadowrocket 配置

个人的 Shadowrocket 规则、模块与脚本集合。

## 订阅

在 Shadowrocket 中：**配置 → 添加配置 → 填入下方地址**

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/default.conf
```

> 本仓库为 Private，raw 地址不能匿名访问。私有仓库需在链接后附加
> token（`?token=...`，GitHub raw 页面「Raw」按钮给出的带签名地址，有效期有限），
> 或将仓库改为 Public。若改 Public，务必先确认没有任何真实节点信息被提交。

## 目录结构

| 目录 | 内容 |
|---|---|
| `config/` | 主配置文件（`.conf`） |
| `rule/` | 自定义分流规则集（`.list`） |
| `module/` | 模块（`.module` / `.sgmodule`） |
| `script/` | 重写脚本（`.js`） |

## 配置说明

`config/default.conf` 包含 41 个策略组、42 个远程规则集，
规则集来源 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)。

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

规则集合计约 19,800 条 / 0.66 MB，对 iOS 网络扩展无压力，无需精简。

### MITM

默认 `enable = false`。URL 重写和去广告脚本需要 MITM 才生效，开启前先在
Shadowrocket 内生成并信任 CA 证书：

设置 → 证书 → 生成新的 CA 证书 → 安装 → 系统「通用 → 关于 → 证书信任设置」中启用

## 关于敏感信息

本仓库**不包含**任何真实的服务器地址、密码或机场订阅链接。

`[Proxy]` 段留空，节点通过 App 内订阅添加即可自动并入各策略组。若要保存本地节点，
复制一份命名为 `*.local.conf` 或 `*.private.conf` —— 这两类文件名已在
`.gitignore` 中排除，不会被提交。

MITM 的 `ca-passphrase` 与 `ca-p12` 是本机私钥，同样不要入库。
