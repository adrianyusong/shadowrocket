# Shadowrocket 配置

个人的代理规则、模块与脚本集合。Shadowrocket / Stash / Clash 三份配置共用一套规则数据。

## 订阅地址

复制对应客户端的地址即可。唯一例外是 Clash 那份 —— 它是模板，必须先填入你自己的订阅地址，见下。

### Shadowrocket

配置 → 添加配置 → 粘贴地址

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/default.conf
```

去广告模块（纯 URL 重写，无脚本）——Config → 右上角 + → 粘贴地址：

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/module/adblock-rewrite.sgmodule
```

### Stash

覆写文件，需与你的机场订阅一起启用：

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/stash.stoverride
```

要合并第二个机场，另取这份模板改本地地址（见「合并多个订阅」）：

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/stash-providers.example.stoverride
```

### Clash for Apple / Clash Verge

> **这份不能直接订阅。** 它是完整 mihomo 配置，`proxy-providers` 的 url 是占位符，
> 直接填进 App 会得到 `DNS lookup failed / Provider "airport"` —— 占位域名本就解析不了。

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/clash.yaml
```

**Clash for Apple（iOS）**：Profile 导入后**不能在 App 内编辑**（详情页只有
update / rename / export / remove），所以必须先改好再导入：

1. 在电脑上下载 `clash.yaml`，把 `proxy-providers.airport.url` 换成你自己的
   Clash 格式订阅地址；两家机场都用时，再取消 `airport-b` 的注释填第二个地址
2. 存成 `*.local.yaml`，放进 iCloud Drive 或 AirDrop 传到手机
3. Clash → Add Profile → **配置文件**（不是 Subscription link）

想保留自动更新就把改好的文件放进**私有**仓库再订阅那个 raw 地址 ——
文件里带着你的订阅地址，绝不能放公开仓库。

**Clash Verge**：直接改本地文件，或用下面的扩展脚本从节点属性动态重建分组。
注意 `clash-verge/` 下的脚本与配置是按旧机场调的，**还没有适配落云**：套在落云上时
伪节点过滤漏掉「请更新订阅」「客户端」和 `🇦🇶` 公告节点，AnyTLS 节点会被当成
Hy2 / VLESS，AI 默认会落到香港。落云请用 `config/clash.yaml`（两家都已适配）。

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/clash-verge/script.js
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/clash-verge/config.yaml
```

### 镜像

`raw.githubusercontent.com` 拉不动时把域名段整体替换（有缓存延迟，通常几分钟到一小时）：

```
https://cdn.jsdelivr.net/gh/adrianyusong/shadowrocket@main/config/default.conf
```

注意这只换了配置本身。配置里的 35 个 `RULE-SET` 仍指向 `raw.githubusercontent.com`，
那边拉不动时规则集照样会下载失败（该类规则静默消失，不报错）。镜像只解决「配置拉不下来」。

> 仓库为 Public，不含任何节点凭据——`[Proxy]` 段留空，`proxy-providers` 是占位地址。
> 你自己的机场订阅地址等同密码，只留在设备上，别写进任何要提交的文件。

## 三份配置，共用一份规则

| | Shadowrocket | Stash | Clash |
|---|---|---|---|
| 配置 | `config/default.conf` | `config/stash.stoverride` | `config/clash.yaml` |
| 形态 | 完整配置 | 覆写（打补丁） | 完整配置 |
| 规则 | `rule/*.list` | `stash/*.txt` | `stash/*.txt` |
| 生成 | 手写（唯一来源） | 从 `default.conf` 推导 | 从 `default.conf` 推导 |
| MITM / 重写 | ✅ | ✅ | ❌ mihomo 不支持 |
| 协议分组 | ❌ | ❌ | ✅ `exclude-type` |

规则数据同源：`tools/sources.txt` → `tools/sync-rules.py` → `rule/*.list`。

**`config/default.conf` 是唯一来源。** Stash / Clash 的规则（顺序、内联规则、注释）
由生成器逐行翻译 `[Rule]` 得到，业务组的候选照抄 `[Proxy Group]`，
`policy-select-name` 体现为「排第一」。改规则或分组只改 `default.conf`，然后跑
`build-stash.py` 与 `build-clash.py`。

> **更正**：之前两个生成器各自手写了一份内联规则与规则顺序，结果漂移了——
> Stash / Clash 把 `DOMAIN-SUFFIX,cn` 与 `apple.com` 排在广告规则集之前，
> Shadowrocket 会拦的 **3402 个广告域名在这两边全部放行**（如 `ad.12306.cn`、
> `searchads.apple.com`）；Steam CDN、MapKit、Claude 等内联规则缺失；Clash 的
> 微软、EMBY 默认出口也与另外两份不同。生成器里还写着「规则顺序与 Shadowrocket 一致」。
> 现在用 14 万个域名全量对照，三份配置分流不一致的为 0；`check-config.py` 的
> `check_derived_parity` 会拦住任何手改生成物或重新塞回手写规则的改动。

### Stash 独有的两点

**规则集按类型拆分。**

> **更正**：早先此处写「Stash 能做协议分组」，是错的。那是查 Mihomo 文档得出的
> 结论，而 Stash 兼容 Clash Premium 但引擎自研 —— 其官方文档的 `proxy-groups`
> 选项里**没有 `exclude-type`**（只有 `filter` / `include-all` / `interval` /
> `lazy` / `strategy` 等）。不支持的选项不报错、只静默忽略，于是「VLESS」组
> 实际等于「全部节点」，Hy2 混在里面。相关分组已全部移除。
>
> `filter` 只能匹配节点名，而机场节点名里没有协议信息，所以这个维度在 Stash 上
> 与 Shadowrocket 同样受限。若机场提供按协议区分的订阅地址，可用
> `proxy-providers` 分别引入再 `use:` 指向，那是另一条路。

**规则集按类型拆分。** Stash 的 rule-provider 有 `domain` / `ipcidr` /
`classical` 三种 behavior，前两种是为海量规则优化的加载器。我们的 `.list`
是混合格式，整份丢给 `classical` 会浪费性能，所以拆成三份各走各的：

```
domain     143,039 条
ipcidr      14,930 条
classical      488 条   （DOMAIN-KEYWORD 等只能走这个）
```

所有 `ipcidr` 规则集引用都带 `no-resolve` —— 不加的话每个域名请求都会为了
判断 IP 归属触发一次本地 DNS 查询，既泄漏域名，又让被污染的解析结果影响分流。

### 用 Stash 的步骤

**订阅地址不进仓库** —— 它带着你的机场凭据。Stash 的模型是订阅作为「配置」在 App
里添加，覆写叠在上面：覆写只替换规则与分组，节点始终来自订阅。

**第一步：添加机场订阅**

配置 → 添加配置 → 从 URL 下载 → 粘贴机场的 **Clash / Stash 格式**订阅地址。

注意格式：机场给 Shadowrocket 的那条（base64 分享链接）Stash 读不了。多数机场
在订阅页会同时给出 Clash 订阅，或者同一地址加 `&flag=clash` / `&flag=stash`。
只有一条通用订阅时，用 subconverter 转一次。

**第二步：添加覆写**

配置 → 覆写 → 添加：

```
https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/config/stash.stoverride
```

**第三步：在订阅配置上启用该覆写**

覆写不会自己生效，要在对应的订阅配置里勾选。

### 为什么覆写里只有一条重写

Shadowrocket 那边现在有内容了：`[URL Rewrite]` 四条（`g.cn` / `google.cn` /
`ditu|maps.google.cn` → Google，加 iRingo MapKit 三条端点重写）、`[Script]` 三条
（iRingo News，版本钉死在 v3.2.1）、`[MITM] hostname` 十个域名。

Stash 覆写只保留 `g.cn` 系列重写，因为 **iRingo 官方直接发布 `.stoverride`**，
自带 `rules` + `http.mitm` + `http.script` + `script-providers`，在 Stash 里作为
独立覆写叠加即可，不需要我们手抄一遍。

`config/clash.yaml` 里这些全都没有 —— mihomo 不支持 MITM。

MITM 本身有代价：要装并信任 CA、解密 HTTPS、耗电。域名列表越小越好，
`tools/check-config.py` 会核对 `[URL Rewrite]` 与 `[Script]` 涉及的每个域名
是否都在 `hostname` 列表里，不在就是死代码。

**iRingo 官方直接发布 `.stoverride`**，自带 `rules` + `http.mitm` +
`http.script` + `script-providers`，在 Stash 里作为独立覆写叠加即可：

| 模块 | 地址 |
|---|---|
| WeatherKit | `https://github.com/NSRingo/WeatherKit/releases/latest/download/iRingo.WeatherKit.stoverride` |
| LocationService | `https://github.com/NSRingo/LocationServices/releases/latest/download/iRingo.LocationService.stoverride` |
| Siri | `https://github.com/NSRingo/Siri/releases/latest/download/iRingo.Siri.stoverride` |
| Spotlight | `https://github.com/NSRingo/Siri/releases/latest/download/iRingo.Spotlight.stoverride` |
| TestFlight | `https://github.com/NSRingo/TestFlight/releases/latest/download/iRingo.TestFlight.stoverride` |
| News | `https://github.com/NSRingo/News/releases/latest/download/iRingo.News.stoverride` |
| TV | `https://github.com/NSRingo/TV/releases/latest/download/iRingo.TV.stoverride` |

Shadowrocket 用户取同一 release 下的 `.sgmodule`，例如
`https://github.com/NSRingo/News/releases/latest/download/iRingo.News.sgmodule`。
本仓库已把 MapKit 的重写与 News 的脚本内联进 `config/default.conf`，
装了模块就不必再启用那两项，否则会重复解密同一批域名。

**叠加时的一个注意点**：本覆写的 `rules` 带 `#!replace`，且以 `MATCH` 收尾。
iRingo 的覆写各自带几条 `REJECT-DROP`（如 `weather-analytics-events.apple.com`），
若本覆写排在它们之后生效，那几条会被 `MATCH` 之前的规则抢先命中而失效。

影响有限——iRingo 的核心功能靠 `http.mitm` 与 `http.script`，与 `rules` 无关，
不受顺序影响。只有那几条埋点拦截可能被遮蔽。要确认的话，在 Data 里看
`weather-analytics-events.apple.com` 是否显示 REJECT-DROP；被遮蔽了就把
iRingo 的覆写排到本覆写之前。

### 合并多个订阅

Stash 一个配置对应一个订阅。要把第二、第三家机场并进来，用 `proxy-providers`
把它们作为「代理集合」引入。

**主覆写不用改** —— 所有地区 / 直连线路 / 线路属性分组都写了 `include-all: true`，
其定义是「包含全部出站代理**和代理集合**」，provider 的节点会自动流进这些分组。

模板见 [`config/stash-providers.example.stoverride`](config/stash-providers.example.stoverride)：

```yaml
proxy-providers:
  airport-b:
    type: http
    url: "第二家的 Clash 格式订阅地址"
    path: ./providers/airport-b.yaml
    interval: 3600
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 300
```

复制模板、填入真实地址、在 Stash 里新建**本地覆写**粘贴进去，与主覆写一起在
同一份订阅配置上启用。

**订阅地址等同凭据，只留在设备上。** 要存本地文件就命名为 `*.local.stoverride`，
该模式已在 `.gitignore` 中排除。

`health-check` 不能省：不开的话 `url-test` / `fallback` 类分组无法判断这批
节点的可用性。

两家若有同名节点会冲突，可用 `override.additional-prefix` 加前缀区分——
是否支持取决于内核版本，加上去若分组变空就说明不支持。

### 分组怎么用

`🚀 节点选择` 的候选全是分组，不含单个节点。要手动指定某个具体节点，用
`🔧 手动选择` —— 它按节点名筛出订阅里全部真实节点（已排除「剩余流量」「到期时间」
这类信息类伪节点与自建 CF 节点）。三份配置都有；Shadowrocket 里它是
`select, policy-regex-filter = …`，手册「代理分组」一节确认 select 组支持正则筛选。

所有按节点名筛选的组都排除了信息类伪节点。之前「剩余流量：412 GB」里的 `GB`
被英国组当成地区标签，把一个 ws 中转伪节点收进了 `🇬🇧 英国直连`。排除词覆盖两家
机场的写法：剩余 / 到期 / 流量 / 重置 / 套餐 / 官网，以及落云的「续费地址」「请更新
订阅」「客户端不对」和整排以 `🇦🇶` 打头的公告节点（其中一个名字只有旗子加空白）。

**两家机场。** 配置按两份订阅同时装载来设计：旧机场（名字带 `CTCU` / `流媒体` /
`0.1x` 这类标签）和落云（`[地区]₁`、`原生`、`家宽` 这类标签）。各组的来源：
- `🛣️ 专线`、`💴 低倍率` 只有旧机场的节点；`🇰🇷 韩国` / `🇰🇷 韩国直连`、`🔒 ANYTLS`
  只有落云的节点。只装一家时对应的组是空的——空的 url-test 组等同 DIRECT，别在 `🚀` 里选它。
- `🏠 住宅IP` 只收住宅 / 家宽（香港 9、台湾 7、越南 3）。`原生` 是在当地 ISP 名下注册的
  机房 IP，不算住宅，归 `🎞️ 流媒体节点`。
- `🎞️ 流媒体节点` = 旧机场的「流媒体」节点 + 落云的「原生」节点，共 8 个国家。它是
  `🎬 DISNEY+` 的默认首选，所以排除 Disney+ 用不了的国家：俄罗斯、越南（未进入），
  马来西亚 / 印尼 / 泰国 / 菲律宾 / 印度（跑在本地 Hotstar 上，只对当地账号开放）。
  东南亚离国内近，url-test 很容易选中它们，不能只靠延迟。
- 落云没有 ws 中转，名字里也没有 `CTCU`，它的地区节点全部算「直连」。
- 泰国、德国、澳洲、加拿大等二十来个其他地区没有单独分组，在 `🔧 手动选择` 里点。

三个维度正交，按需切换：

| 维度 | 分组 |
|---|---|
| 线路属性 | `🏠 住宅IP` `🛣️ 专线` `🎞️ 流媒体节点` `💴 低倍率` |
| 地区 | 港 / 台 / 日 / 新 / 美 / 韩 / 英（韩国只有落云的节点） |
| 直连线路 | `🇺🇲 美国直连` `🇯🇵 日本直连` `🇸🇬 狮城直连` `🇭🇰 香港直连` `🇹🇼 台湾直连` `🇬🇧 英国直连` `🇰🇷 韩国直连`（地区 × 无中转，三份配置都有） |

| 协议 | **仅 Clash**：`🔐 VLESS` `⚡ HY2` `🔒 ANYTLS`（落云以 AnyTLS 为主；只列订阅里有的协议——筛完为空的组在 mihomo 里只剩 COMPATIBLE，等同 DIRECT；VMESS / TROJAN 因此移除，有节点后在 `build-clash.py` 加回） |

**协议维度在 Shadowrocket 与 Stash 上做不到**，原因见上方更正。
`tools/check-config.py` 会拒绝任何 Stash 未记载的 `proxy-groups` 选项，
防止再写出这类静默失效的配置。

`config/clash.yaml` 能做，因为 mihomo 真的实现了 `exclude-type`
（`adapter/outboundgroup/groupbase.go`）。但有个坑：group 层比较的是
`p.Type().String()`（`Vless` / `Shadowsocks`），不是配置里的 `type:` 值
（`vless` / `ss`）。`EqualFold` 让 `hysteria2` 能对上 `Hysteria2`，
但 **`ss` 对不上 `Shadowsocks`** —— 网上抄来的 `exclude-type: "ss|ssr|..."`
在 group 上排不掉 SS 节点，而且不报错。`check_clash` 会拦这个写法。

## 目录结构

| 目录 | 内容 |
|---|---|
| `config/` | 三份主配置（`.conf` / `.stoverride` / `.yaml`） |
| `rule/` | 分流规则集（`.list`），已收编上游 |
| `module/` | 模块（`.module` / `.sgmodule`） |
| `stash/` | Stash / Clash 共用的规则集（`.txt`） |
| `clash-verge/` | Clash Verge Rev 扩展脚本与独立配置 |
| `script/` | 重写脚本（`.js`） |
| `tools/` | 维护脚本 |

### 规则集为什么收编进仓库

原先配置直接引用 54 个 blackmatrix7 的远程规则集。每次配置更新就是 54 次网络请求，
任何一个失败该类规则会**静默消失**，不报错。上游改动也会在你不知情时改变分流。

现在全部收编进 `rule/`，共 35 个文件、15.8 万条规则，再派生出 `stash/` 下 83 个按 behavior 拆分的规则集。

### 维护

| 文件 | 作用 |
|---|---|
| `tools/sources.txt` | **上游清单，脚本的唯一数据来源**。顺序只决定同一策略内多个上游的先后；跨策略的优先级由 `default.conf` 的 `[Rule]` 决定 |
| `tools/exclude.txt` | 上游缺陷黑名单，同步时剔除 |
| `tools/sync-rules.py` | 拉取、去重、按策略合并 |
| `tools/sync-modules.py` | 收编上游模块到 `module/` 自托管 |
| `tools/build-stash.py` | 生成 Stash 覆写与 `stash/*.txt` |
| `tools/build-clash.py` | 生成完整 mihomo YAML |
| `tools/check-config.py` | 静态校验，失败退出码非 0 |
| `.github/workflows/sync.yml` | 每周日自动同步，校验通过才提交 |

```bash
python tools/sync-rules.py \n  && python tools/sync-modules.py \n  && python tools/build-stash.py \n  && python tools/build-clash.py \n  && python tools/check-config.py
```

`sources.txt` 必须独立存在：规则集收编后配置里只剩指向本仓库的 URL，
脚本再也无法从配置反推上游。

`sync-rules.py` 拉取后**全局去重** —— 同一条规则只保留首次出现的策略。「首次」按
`default.conf` `[Rule]` 里 `RULE-SET` 的先后算，与设备的匹配顺序一致；只处理完全
相同的规则，关键词、宽后缀这类「重叠而非重复」的规则仍由 `[Rule]` 顺序决定谁先命中。

> **更正**：之前按 `sources.txt` 的顺序去重，而设备按 `[Rule]` 匹配，两个顺序有
> 181 对前后颠倒——你在 `[Rule]` 里排的优先级对重复规则不起作用。切换后 15.8 万条里
> 有 66 条换组，逐条核对：多数是改对了（Tailscale 的 `ts.net` 与极路由后台改直连、
> 港台 B 站 IP 改走国外媒体、Minecraft 等游戏域名归游戏平台），逐条核对出 `snssdk.com`
> （抖音与 TikTok 共用）和 Apple TV 的 UA 需要例外；之后用真实日志重放又查出
> `digicert.com`、`akadns.net`、`xboxlive.com/.cn` 三处，都已写进 `exclude.txt`。

同时把 QuantumultX 的 `HOST-SUFFIX` 归一化为原生 `DOMAIN-SUFFIX`，并给**所有**
IP 类规则补上 `no-resolve`（见下文「DNS 泄漏」）。

`exclude.txt` 记录上游缺陷。收编后上游的错误也进了本仓库，手工删除会在下次同步时
被搬回，所以必须记在这里。当前三十余条（`sync-rules.py` 运行时会打印准确条数），
每条都有实测依据，按类别举例。
值以 `*.` 开头的按后缀匹配，例如 `DOMAIN-SUFFIX,*.cn,game` 剔除游戏集里所有 `.cn` 后缀：

- `DOMAIN-KEYWORD,jav` —— 误伤 `javascript.info`、`javadoc.io`
- `DOMAIN-SUFFIX,ms` —— `.ms` 是蒙特塞拉特国家域，非中国 gTLD
- `DOMAIN-SUFFIX,simility.com,reject-ads` —— PayPal 风控引擎，只从广告策略剔除
- `DOMAIN-SUFFIX,grok.com,twitter` —— Grok 属 X 旗下被 Twitter 集抢走，让 AI 集接管
- `DOMAIN-SUFFIX,stripe.com,ai` —— OpenAI 用 Stripe 收款，通用支付商不该进 AI 组
- `dns.weixin.qq.com.cn` 与 `101.124.19.122` —— 微信 HTTPDNS，被误判为广告，小程序因此打不开
- `openx` / `adform` / `adservice` / `adsystem` / `wixsite.com` / `m.suning.com` 关键词 ——
  误伤所有 Wix 站、苏宁移动站、openxlab.org.cn 等。主投放域名 anti-AD 都有精确后缀；
  关键词顺带拦住、却没有精确后缀的（各国家后缀的 `adservice.google.<cc>`、
  `servedbyopenx.com`、`adformdsp.net`）在 `default.conf` 广告段用更窄的规则补回
- `crl.microsoft.com` —— 证书吊销列表，拦掉会让证书校验失败
- `in.th` —— 泰国域名被归进了国内集
- `thunder` 关键词 —— 本意是迅雷，却把 thunderbird.net、warthunder.com 送去直连
- `smoot.apple.com` —— 被 AI 集顺带收走，抢在 iRingo 内联规则之前
- B 站国际版的 API 与 CDN —— 被收进国内媒体走直连
- 去重改按 `[Rule]` 顺序后的五处例外：`snssdk.com`（抖音与 TikTok 共用）、
  Apple TV UA，以及真实日志重放查出的 `digicert.com`（被巴哈姆特集收走）、
  `akadns.net`（Apple 推送被微软集收走）、`xboxlive.com/.cn`（下载 CDN 被游戏集收走）
- 游戏集的 `.cn` 后缀（29 条：暴雪国服、腾讯 Switch、英雄联盟、索尼中国等）——
  只在国内提供，却被默认走代理的游戏组抢在 `.cn` 直连之前

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

`config/default.conf` 包含 55 个策略组、35 个规则集（全部托管于本仓库 `rule/`，
来自 70 个上游），合计 15.8 万条规则。上游为 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)
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

直连线路分组是「地区 × 无中转」：只收节点自身直接落地的线路，排除前置 Cloudflare
的中转节点。中转节点出口是共享边缘 IP，支付风控与 AI 服务对它命中率高，长连接也
容易中断。`🚀 节点选择` 与 `🤖 AI 服务` 都以 `🇺🇲 美国直连` 为首选。这里的「直连」
与 `DIRECT` 策略无关。

**判据：节点名里单独出现的 `CTCU` 标签 = 中转。** 用机场订阅文件的真实传输方式核对过
（`network: ws` 即 Cloudflare 中转）：

| | 节点数 | 带裸 `CTCU` |
|---|---|---|
| 中转 | 15 | 15 |
| 直连 | 39 | 1（`🇬🇧英国伦敦02`，reality 直连，会被误排除） |

误排除是安全方向的错，只是少一个可用节点，不会把中转混进来。`CTCUCM` 是三网直连
标签，所以正则写成 `(?<![A-Za-z])CTCU(?![A-Za-z])`，只截单独的 `CTCU`。

> **更正**：最初版本（#1）把 `CTCU` 当成「美国直连」的正向条件，结果 `🇺🇲 美国直连`
> 选中的 5 个节点**全是中转**，`🇸🇬 狮城直连` 也混进 3 个。原先的
> `(?!.*(中转|中轉|隧道|转发|轉發))` 排除条件从未生效——这家机场的中转节点名字里
> 根本没有这些字。节点名本身看不出对错，所以这个错一直没被发现，直到拿真实数据对照。
>
> 同一段里「mihomo 走 RE2，`(?=` 得改写」也不对：mihomo 用的是 `dlclark/regexp2`
> （`adapter/outboundgroup/parser.go`），.NET 兼容，支持前后环视。所以三份配置
> 用的是同一条带环视的正则。

**这个判据只对当前机场的命名成立。** 换机场或机场改名后要重新核对：

```bash
python tools/check-direct.py 旧机场订阅.yaml 落云订阅.yaml
```

它只读订阅文件里每个节点的 `name` / `type` / `network` 三个字段，以 `network`
为真实依据检查三份配置里的分组。返回 1 的情形：直连组混进中转、任何组收进伪节点，
或直连组 / 被 select 组当默认首选的组筛出来为空——空的 url-test 组在 Shadowrocket
里只剩 DIRECT、在 mihomo 里只剩 COMPATIBLE，默认流量会悄悄直连。其余被引用的空组
只告警。Clash 的协议分组按节点真实 `type` 计算成员。订阅文件含服务器地址与凭据，
只在本地读取，不写出任何字段，也不进仓库——Clash Verge 的 `profiles` 目录里就有现成的。

`tools/check-config.py` 在 CI 里守结构：每个直连组必须带中转排除子句、不能把
`CTCU` 写成正向条件、三份配置的直连组必须一致。CI 拿不到私有订阅，所以真实数据
的核对只能靠上面那个本地工具。

可以一次传多份订阅，按合并后的节点核对——两家都装时就该这样查。只传旧机场一份时，
`🇰🇷 韩国直连` 为空会报 FAIL（直连组为空一律 FAIL），这是预期的。

### DNS 泄漏与 DNS 服务器

**所有 IP 类规则都带 `no-resolve`。** 缺一条就会泄漏：求值走到它时，域名要先经
本地（国内）DNS 解析才能判断 IP 归属。之前 `apple.list` 13 条、`china.list` 19 条、
`proxy.list` 56 条都缺——Apple 源取的是 QuantumultX 格式，IP 行不带这个修饰符，而
`check-config.py` 只查 `china.list` 且容忍 10% 缺失，所以一直通过。泄漏范围取决于
Shadowrocket 的求值方式：严格逐行时 `apple.list` 之后所有没被前面域名规则接住的域名
都会被解析；按手册「域名类的规则优先于 IP 类规则」则只有最终落到 FINAL 的会被解析。
现在 `sync-rules.py` 生成时强制补上，`check-config.py` 查全部文件、零容忍。

代价：域名层没覆盖到的国内站点不再有「解析后按 IP 判国内」的兜底，会落到 FINAL
走代理。用两份真实日志核对过，161 个域名里受影响的为 0；复核时抽查到的得物、唯品会、
贝壳、中通、快手 / 火山 CDN、飞书、kimi、豆包等 25 个已补进 `rule/ChinaDomains.list`。
发现别的国内服务走了代理，补进这个文件即可。

**`dns-server` 只放 DoH。** 手册原文是「并行查询，最先返回的结果将被采用」——
之前末尾的明文 `223.5.5.5` 并不是「DoH 失效时的兜底」，而是与 DoH 赛跑，而明文通常
更快，于是大部分解析走的是可被劫持的明文。现在用 IP 直连的 DoH（`https://223.5.5.5/dns-query`、
`https://1.12.12.12/dns-query`，免去解析 DoH 主机名本身）。真正的兜底是
`fallback-dns-server = system`，手册写的触发条件是「查询失败」「未返回有效响应」或
超过 2 秒。`router.asus.com` 这类只在局域网存在的名字，公共 DoH 会明确答 NXDOMAIN——
NXDOMAIN 算不算「查询失败」手册没写，也没在设备上验证，别指望靠它；进路由器后台
直接用路由器 IP。

**`hijack-dns` 覆盖国内公共 DNS。** 规则全带 `no-resolve` 之后，唯一还能绕过去的是
App 自己硬编码的明文 DNS：发往 `114.114.114.114:53`、`223.5.5.5:53`、
`119.29.29.29:53` 的查询按目的 IP 命中 `GEOIP,CN` 走直连，App 查的任何域名（包括
境外的）都以明文交给国内 DNS。之前只劫持了 `8.8.8.8` / `8.8.4.4` / `1.1.1.1` 三个
境外地址——恰好是不会泄漏的那一半（它们随 FINAL 走代理）。现在补齐 114、阿里、
腾讯 / DNSPod、百度、CNNIC、360 共 12 个国内地址，外加 Quad9、OpenDNS 等境外地址。
逐个列出而不写 `*:53`：社区手册只举了具体地址的写法，通配是否支持未经验证。
`check-config.py` 的 `check_hijack_dns` 会在国内地址缺失时报错。

**`skip-proxy` 只放规则本来就判直连的域名。** `skip-proxy` 在规则层之前生效，
命中即绕过，解析也脱离本配置的 DNS 设置。Surge 模板遗留的 `*.crashlytics.com`
在规则里归 `🛑 广告拦截`，放在这里等于让拦截失效，已移除。以前的检查只看 Apple
域名才一直没发现；现在 `check_skip_proxy` 对每个域名按规则求值，判拦截或判代理的
都报错（`captive.apple.com` 除外，见下文 Apple 一节）。

**自测用 ipleak.net 或 dnsleaktest.com。** 结果里只出现机场出口所在地的 DNS 为正常；
出现阿里、腾讯或本地运营商才是泄漏。`browserleaks.com` 被上游国内规则误收、会走直连，
测出来必然是国内 DNS——已在 `exclude.txt` 从国内策略剔除，现在随 FINAL 走代理，也能用了。

### IP 稳定性

`🤖 AI 服务` 的候选中**刻意不放** `🚀 节点选择` —— 它可间接指向 `🔮 负载均衡`，
导致每个请求换一次出口，会被 OpenAI 判定异常。AI 组以直连线路组打头，默认
`🇺🇲 美国直连`；不放 `🏠 住宅IP`，因为它混着 9 个香港住宅节点，而 OpenAI /
Claude / Gemini 都不对香港开放。

YouTube / Netflix / Disney+ / HBO / Prime Video / 巴哈姆特 / AbemaTV 把流媒体或
地区组排在 `🚀` 之前，默认不落到它，`🚀` 只作备选（之前文档说「流媒体组不放 🚀」，
与实际不符）。Spotify / TikTok / Twitch / 国外媒体 / PikPak 则以 `🚀` 打头、默认
跟随它——把 `🚀` 切到 `🔮 负载均衡` 时，它们也会跟着每请求换出口。
HBO / Prime Video 以 `🇺🇲 美国直连` 打头，
旧机场的 7 个美国直连节点全带流媒体标签、落云的 4 个美国节点全是原生 IP；
Netflix / Disney+ 没有这样的单一地区组，仍以跨 8 个国家的 `🎞️ 流媒体节点` 打头。

地区分组的测速间隔调为 `interval = 600, tolerance = 200` 抑制抖动。
要彻底稳定，选 `🔧 手动选择` 再点具体节点。

### 规则顺序

有几处是刻意安排的，改动时注意：

| 位置 | 原因 |
|---|---|
| 埋点 `REJECT-DROP` 在广告规则之前 | 否则用的是普通 REJECT，抑制不了重试风暴 |
| 广告 / 隐私规则靠前 | 拦截优先于分流，否则被后面的域名规则抢先命中 |
| AI 服务在 Global / Microsoft / Google 之前 | 否则 OpenAI、Copilot 会被宽泛规则集吞掉 |
| GoogleFCM 随 Google 走 📢 谷歌服务 | 国内直连连不上，推送整个失效（之前走 DIRECT 是错的） |
| AppleID 随 Apple 走 🍎 苹果服务 | 默认经 🚀 走代理，不是直连（两者已合并进同一个 `apple.list`，无先后可言） |
| Bing 在 Microsoft 之前 | 否则被 Microsoft 规则集吞掉走直连 |
| LinkedIn 在 `.cn` 与 `GEOIP,CN` 之前 | 国内 DNS 把 `linkedin.com` 解析到国内 IP，GEOIP 会误判成国内服务 |
| `jpush.cn` 的 `REJECT-DROP` 在广告规则与 `.cn` 之前 | `reject-ads.list` 也收了 `jpush.cn`，排在它后面就只是普通 REJECT，抑制不了重试；上游不收时还会被 `.cn` 当成国内服务放行 |
| 游戏 CDN 内联规则在 Steam / Blizzard 之前 | 见下 |
| BiliBiliIntl 在 BiliBili 之前 | 国际版需代理，国内版直连 |
| `adservice.google.` 关键词在 Google 之前 | anti-AD 的短关键词已剔除（误伤太多），各国家后缀的 adservice 靠它补回 |
| `android.apis.google.com` 在 AI 之前 | `ai.list` 的 `apis.google.com` 后缀会把这个 FCM 端点抢进 AI 组 |
| 游戏集不含 `.cn` 域名 | 游戏组排在 `.cn` 之前且默认代理，暴雪国服、腾讯 Switch 等会被拖去境外；由 `exclude.txt` 的 `DOMAIN-SUFFIX,*.cn,game` 剔除 |
| Speedtest 走直连 | 测本地真实带宽 |
| `china.list` + `GEOIP,CN` 在 FINAL 之前 | 域名规则先行，IP 兜底 |

这些约束由 `tools/check-config.py` 验证，破坏或标记缺失都会 FAIL：
- `ORDER` 核对位置（哪一行必须早于哪一行）；
- `MUST_ROUTE` 核对去向：每一行挑一两个关键主机，按 `[Rule]` 模拟首条命中，
  核对它最终落到哪个策略。模拟只覆盖域名类规则与规则集里的域名条目，`GEOIP`、
  `IP-CIDR`、`FINAL` 不在其中——`GEOIP,CN` 那半行只核对它的位置与策略（须为
  `🇨🇳 国内服务` 且带 `no-resolve`），不核对 IP 兜底的实际效果。

### QUIC

用 `[General]` 的 `block-quic = all-proxy`，不再用 `[Rule]` 里的
`AND,((PROTOCOL,UDP),(DST-PORT,443))`。

后者对所有连接生效，日志里 5990 条 QUIC 拒绝中相当一部分是国内 App 被迫回落
TCP，属于纯损失。而阻断 QUIC 的两个理由——绕过 TCP 侧规则匹配、流媒体解锁判定
不稳——只对代理连接成立。`all-proxy` 正好只管代理连接。

### 游戏下载为什么要单独拦

`🎮 游戏平台` 是代理优先（商店、社区、登录需要代理），但 `Steam.list` 与
`Blizzard.list` 内部混着下载 CDN 域名，整组走代理会把几十 GB 的游戏本体
拖进代理烧套餐。`SteamCN.list` 只覆盖国区 CDN，实测 `Steam.list` 里有 11 个
全球下载 CDN 域名不在其中（含 `steampipe.akamaized.net`、
`steamcdn-a.akamaihd.net`），Blizzard 另有 6 个（含主下载 CDN
`blzddist1-a.akamaihd.net`）。`Download.list`（已并入 `direct-global.list`）只收
下载工具，不含这些 CDN，救不了。

所以这 18 条域名（另加 Minecraft 的 5 个下载主机）以内联 `DOMAIN-SUFFIX` 规则
显式拦到直连，位置在游戏规则集之前。

### Apple 全量走代理

`🍎 苹果服务` 为代理优先，`AppleID` 也归入该组（不再单独直连）。

`apple.list` 现有 1869 条，已含 `apple.com` / `icloud.com` 全域后缀。早先它只有
41 条、没有全域后缀，大量 Apple 域名解析到国内 IP 后被 `GEOIP,CN` 判成国内服务走了
直连（日志实测 `ocsp2.apple.com` 286 次、`gs-loc-cn.apple.com` 169 次）。当时补的
12 条内联域名后缀加 `IP-CIDR,17.0.0.0/8`（Apple 独占段）仍保留，位置在 `china.list`
与 `GEOIP,CN` 之前，只作 `apple.list` 拉取失败时的保险。

**唯一例外**：`captive.apple.com` 保留在 `skip-proxy`。那是 WiFi 门户检测，
`skip-proxy` 在规则之前生效、直接绕过隧道；走代理会导致连不上酒店与机场热点。

**代价**：

| 影响 | 后果 |
|---|---|
| APNs 推送 | `push.apple.com` 走代理，推送可能延迟或丢失 |
| App Store 下载 | 应用动辄几百 MB，全走机场流量 |
| iCloud 备份 / 照片同步 | 流量可能很大 |
| Apple ID 登录 | 走代理易触发二次验证 |
| 中国区 iCloud | 服务器在云上贵州，代理反而更慢 |

要回退某一项，把对应域名单独加一条 `DIRECT` 规则，位置放在这批规则之前即可。

### WebRTC 防泄漏

```
stun-response-ip = 1.1.1.1
stun-response-ipv6 = ::1
```

浏览器与部分 App 通过 STUN 探测公网地址，那条路径不经过代理，是绕过隧道
暴露真实 IP 的经典途径。开启后 STUN 只能拿到固定假值。

**代价**：P2P 打洞失败。FaceTime、微信视频、腾讯会议、Discord 语音会被迫
走中继，接通变慢、质量下降，个别情况连不上。遇到通话问题就注释掉这两行，
立即恢复，不需要改别处。

### 已知不确定项

`[General]` 中的 `private-ip-answer` 语义未在设备上验证。若国内域名解析异常，
优先注释该行排查。

引入 anti-AD 后规则总量从 2.3 万涨到 12.3 万，iOS 网络扩展的加载表现未在设备上
验证。若出现启动慢或内存告警，从 `tools/sources.txt` 移除 anti-AD 一行后重跑同步即可。

### MITM

`enable = true`。这里若写 false，每次拉取配置都会把 App 内的开关按回去，
表现为模块间歇性失效。

`hostname` 现在有十个域名：`*.g.cn` / `*.google.cn`（跳转重写）、
`configuration.ls.apple.com` 与 `gspe35-ssl.ls.apple.com`（iRingo MapKit）、
六个 `news-*.apple.com`（iRingo News）。App 内另装的模块用
`hostname = %APPEND% xxx` 追加，会并入而非替换本列表。

**列表越小越好**——每多一个域名就多一份明文流量被解密。
`check-config.py` 会核对 `[URL Rewrite]` 与 `[Script]` 里每个 pattern 涉及的
域名是否都在 `hostname` 中：不在就是永不触发的死代码，这正是当初
`g.cn` 那两条重写被删掉的原因。

自托管的 `module/adblock-rewrite.sgmodule` 会再解密 902 个域名。
做成独立模块而非写进配置，就是为了出问题能一键关掉。

证书需在 App 内生成并**在系统里信任**：

设置 → 证书 → 生成新的 CA 证书 → 安装 → 系统「通用 → 关于 → 证书信任设置」中启用

## 关于敏感信息

本仓库**不包含**任何真实的服务器地址、密码或机场订阅链接。

`[Proxy]` 段留空，节点通过 App 内订阅添加即可自动并入各策略组。若要保存本地节点，
复制一份命名为 `*.local.conf` 或 `*.private.conf` —— 这两类文件名已在
`.gitignore` 中排除，不会被提交。

MITM 的 `ca-passphrase` 与 `ca-p12` 是本机私钥，同样不要入库。
