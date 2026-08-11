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

### 首次加载必须检查

自动测速类分组（`♻️ 自动选择`、`🔯 故障转移`、`🔮 负载均衡`）与 6 个地区分组
使用 `policy-regex-filter` 按**节点名关键词**匹配订阅节点。加载后请确认这些分组
里有节点：

- **有节点** → 正常
- **空的** → Shadowrocket 版本不支持该语法，或你的节点名不含地区关键词。
  需改为显式列出节点名：`♻️ 自动选择 = url-test, 节点A, 节点B, url = ..., interval = 300`

分组为空时策略链会静默失效（全部走直连），不会报错，所以这步不能跳过。

### 规则顺序

有几处是刻意安排的，改动时注意：

| 位置 | 原因 |
|---|---|
| QUIC 阻断在最前 | UDP 443 会绕过 TCP 侧规则匹配，也让流媒体解锁判定不稳 |
| 广告 / 隐私规则靠前 | 拦截优先于分流，否则被后面的域名规则抢先命中 |
| AI 服务在 Global / Microsoft / Google 之前 | 否则 OpenAI、Copilot 会被宽泛规则集吞掉 |
| GoogleFCM 在 Google 之前且走 DIRECT | FCM 走代理收不到推送 |
| AppleID 在 Apple 之前且走 DIRECT | 走代理易触发二次验证 |
| BiliBiliIntl 在 BiliBili 之前 | 国际版需代理，国内版直连 |
| Download / Speedtest 走直连 | 前者不消耗套餐，后者测本地真实带宽 |
| ChinaMax + GEOIP,CN 在 FINAL 之前 | 域名规则先行，IP 兜底 |

### 已知不确定项

`[General]` 中的 `private-ip-answer` 语义未在设备上验证。若国内域名解析异常，
优先注释该行排查。旧设备内存吃紧时，可把 `ChinaMax` 换成体积更小的 `China`。

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
