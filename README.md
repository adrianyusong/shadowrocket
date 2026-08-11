# Shadowrocket 配置

个人的 Shadowrocket 规则、模块与脚本集合。

## 目录结构

| 目录 | 内容 |
|---|---|
| `config/` | 主配置文件（`.conf`） |
| `rule/` | 分流规则集（`.list`） |
| `module/` | 模块（`.module` / `.sgmodule`） |
| `script/` | 重写脚本（`.js`） |

## 使用

在 Shadowrocket 中通过原始文件地址订阅，例如：

```
https://raw.githubusercontent.com/<用户名>/<仓库名>/main/config/default.conf
```

规则集与模块同理，替换成对应路径即可。

## 关于敏感信息

本仓库**不包含**任何真实的服务器地址、密码或机场订阅链接。

`config/default.conf` 是不含凭据的模板。真实配置请复制一份并命名为
`*.local.conf` 或 `*.private.conf` —— 这两类文件名已在 `.gitignore` 中排除，
不会被提交。

如果仓库设为 public，提交前请再确认一遍 `[Proxy]` 段和订阅链接没有被写进去。
