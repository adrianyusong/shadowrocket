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

`config/default.conf` 包含：

- **DNS** — 国内 DNS 直连解析，劫持 App 发往 8.8.8.8 / 1.1.1.1 的请求防绕过，
  污染应答（返回私有 IP）直接丢弃
- **策略组** — 主策略（手动 / 自动测速 / 故障转移 / 负载均衡）+ 21 个分类组
- **分流规则** — 32 个远程规则集，来源 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)

规则顺序上有几处是刻意安排的，改动时注意：

| 位置 | 原因 |
|---|---|
| 广告 / 隐私规则最靠前 | 拦截优先于分流，否则会被后面的域名规则抢先命中 |
| AI 服务在 Global / Microsoft 之前 | 否则 OpenAI、Copilot 会被宽泛规则集吞掉 |
| GoogleFCM 在 Google 之前且走 DIRECT | FCM 走代理会收不到推送 |
| Download 走直连 | 大流量下载不消耗机场套餐 |
| ChinaMax + GEOIP,CN 在 FINAL 之前 | 域名规则先行，IP 兜底 |

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
