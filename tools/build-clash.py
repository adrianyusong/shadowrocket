#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成完整 mihomo YAML —— 给 Clash for Apple / Clash Verge 用。

与 build-stash.py 的区别：
  Stash 那份是「覆写」(.stoverride)，靠 #!replace 往订阅上打补丁；
  这份是**完整配置**，因为 clash.md 文档明确要求把覆写合并成标准 mihomo YAML
  （"consolidate override files into a clear mihomo YAML configuration"）。

策略组、地区正则、规则集全部从 build-stash.py 导入，避免两份配置漂移。

协议分组在这里是可行的（Stash 上不行）——见 PROTOCOLS 处的注释。
"""
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'config', 'clash.yaml')

# 从 build-stash.py 取常量：策略映射、地区正则、线路属性只有一份定义。
_spec = importlib.util.spec_from_file_location(
    'bs', os.path.join(ROOT, 'tools', 'build-stash.py'))
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)

BASE = bs.BASE
TEST_URL = bs.TEST_URL

# mihomo 的全部节点协议（constant/adapters.go 的 AdapterType.String()）。
# 注意大小写形式：group 层的 exclude-type 比较的是 p.Type().String()，
# 不是配置里的 type: 值。EqualFold 让 hysteria2/Hysteria2 能对上，
# 但 ss ≠ Shadowsocks、ssr ≠ ShadowsocksR——照抄网上的 "ss|ssr|..."
# 在 group 上排不掉 SS 节点。这里一律用 AdapterType 名。
# 漏掉任何一个类型，它就会漏进**全部**协议分组——因为分组是靠
# 「排除其余所有类型」实现的，而且同样不报错。
# 首次写这份清单时一口气漏了 8 个（Sudoku / Masque / TrustTunnel /
# ShadowQuic / OpenVPN / Tailscale / ZeroTier / GostRelay），是 Shadowrocket
# 2.2.92 加入 Sudoku 才暴露出来的。tools/sync-modules.py 会拉 Hako 锁定的
# mihomo 版本（v1.19.30）比对，漏了在 CI 摘要里告警（不阻断同步）。
# EasyTier 只在 mihomo 的 Alpha 分支有、v1.19.30 没有：留着无害（名字对不上
# 就不起作用），等 Hako 升级内核时也不用再补。
ALL_TYPES = ['Shadowsocks', 'ShadowsocksR', 'Snell', 'Socks5', 'Http',
             'Vmess', 'Vless', 'Trojan', 'Hysteria', 'Hysteria2',
             'WireGuard', 'Tuic', 'Ssh', 'Mieru', 'AnyTLS', 'Sudoku',
             'Masque', 'TrustTunnel', 'ShadowQuic', 'OpenVPN',
             'Tailscale', 'ZeroTier', 'EasyTier', 'GostRelay']

# 控制器密钥占位符。生成器不能写入真实密钥——仓库是 Public。
# 不含引号：带引号的占位符会破坏生成的 YAML（已实测踩到）。
# check-config.py 会在它仍是占位符时告警。
SECRET_PLACEHOLDER = 'CHANGE-ME-generate-a-random-secret'

# 协议分组。Stash 上做不到（exclude-type 被静默忽略，Hy2 会混进 VLESS 组），
# mihomo 的 adapter/outboundgroup/groupbase.go:211 实现了它，所以这里能做。
# 没有 include-type，只能反着排除其余全部类型。
#
# 只列订阅里确实有的协议。筛完为空的组在 mihomo 里只剩 COMPATIBLE（等同 DIRECT），
# 挂在 🚀 / 🤖 上就是一个标着协议名、实际直连的开关——与 🇰🇷 韩国 被移除同理。
# 当前订阅只有 vless 与 hysteria2；有 vmess / trojan 节点后把下面两行加回，
# 并用 tools/check-direct.py 核对（它会按节点真实 type 算出每个协议组的成员）。
#   ('🧩 VMESS 节点',  'Vmess'),
#   ('🐴 TROJAN 节点', 'Trojan'),
PROTOCOLS = [
    ('🔐 VLESS 节点',  'Vless'),
    ('⚡ HY2 节点',    'Hysteria2'),
]


def q(s):
    """YAML 双引号字符串。正则里的反斜杠必须转义。

    不转义的话 YAML 会把 \\. 当成转义序列而报 unknown escape character。
    与 build-stash.py 的 q() 保持一致。
    """
    return '"%s"' % s.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')


def main():
    out = []
    A = out.append

    A('# Clash for Apple / Clash Verge 完整配置')
    A('# 由 tools/build-clash.py 生成，请勿手改——下次同步会覆盖。')
    A('# https://github.com/adrianyusong/shadowrocket')
    A('#')
    A('# ============================================================')
    A('# 这是模板，不是可直接订阅的地址。')
    A('#')
    A('# 下面 proxy-providers 的 url 是占位符，直接订阅本文件会得到')
    A('# 「DNS lookup failed / Provider airport」——占位域名本就解析不了。')
    A('#')
    A('# Clash for Apple 的 Profile 导入后不能在 App 内编辑（详情页只有')
    A('# update / rename / export / remove），所以必须先改好再导入：')
    A('#   1. 在电脑上下载本文件，改两处：')
    A('#      a) proxy-providers.airport.url  -> 你的订阅地址')
    A('#      b) secret                       -> 你自己生成的随机串')
    A('#         生成：python -c "import secrets;print(secrets.token_urlsafe(32))"')
    A('#   2. 存成 *.local.yaml，放进 iCloud Drive 或用 AirDrop 传到手机')
    A('#   3. Clash → Add Profile → 选「配置文件」导入')
    A('# Clash Verge 直接改本地文件即可。')
    A('#')
    A('# 订阅地址等同凭据，*.local.yaml 已在 .gitignore 排除，别提交。')
    A('# ============================================================')
    A('#')
    A('# 与 Shadowrocket 配置的差异：mihomo 没有 MITM，所以这份配置里')
    A('# 没有 iRingo MapKit/News、没有 URL 重写去广告、没有 g.cn 跳转。')
    A('# 域名级广告拦截（10 万条 reject 规则）照常生效，URL 级重写去广告没有。')
    A('')

    # ---- 基础 ----
    A('mixed-port: 7890')
    A('allow-lan: false')
    A('mode: rule')
    A('log-level: info')
    A('ipv6: false')
    A('# 统一延迟基准：扣掉握手耗时再比较，否则 Hy2 会因为握手快而虚高。')
    A('# clash.md 的配置参考里明确列为 Supported（Stash 文档无此项，故未启用）。')
    A('unified-delay: true')
    A('tcp-concurrent: true')
    A('keep-alive-interval: 30')
    A('# iOS/tvOS 的 NetworkExtension 拿不到进程信息，开了也只是空转。')
    A('find-process-mode: "off"')
    A('global-client-fingerprint: chrome')
    A('')
    A('# ============================================================')
    A('# 外部控制器：让电脑实时读取连接与日志（配合 tools/tail-clash.py）')
    A('#')
    A('# clash.md 的字段表把 external-controller / secret / bind-address')
    A('# 标为 Advanced —— 支持，但官方 best-practice 明确不建议暴露到局域网。')
    A('# 这里是明知代价而为之，以下三条必须看懂再用：')
    A('#')
    A('# 1) secret 为空 = 整个 API 无鉴权。mihomo 的鉴权中间件写作')
    A('#    if secret != "" { r.Use(authentication(secret)) } —— 空值直接不挂载。')
    A('#    下面的占位符必须换掉，换成占位符本身也等于用了一个公开的密码。')
    A('#')
    A('# 2) PUT /configs 一个请求就能替换整份配置（代理、规则、DNS）。')
    A('#    也就是说拿到 API 的人可以把你手机的全部流量导向他自己的服务器，')
    A('#    不需要任何代码执行。这是最坏情况，比读日志严重得多。')
    A('#')
    A('# 3) mihomo 没有针对控制器的来源 IP 白名单 —— lan-allowed-ips 管的是')
    A('#    代理入站，不是控制 API。绑到局域网时唯一的保护就是这个 secret。')
    A('#    绝不要在咖啡厅 / 酒店 / 公司 / 合租的网络上开。')
    A('#')
    A('# 只想本机用（Clash Verge 桌面版）就把地址改回 127.0.0.1:9090。')
    A('# ============================================================')
    A('external-controller: 0.0.0.0:9090')
    A('secret: %s' % q(SECRET_PLACEHOLDER))
    A('external-controller-cors:')
    A('  # 默认不给任何网页来源。tools/tail-clash.py 是 Python 客户端，')
    A('  # 不受 CORS 限制，所以留空即可。')
    A('  allow-origins: []')
    A('  # mihomo 的默认值是 true —— 那会让你访问的任何网站都能用 JS')
    A('  # 在后台驱动这个控制器。这才是现实中的攻击路径。')
    A('  # 要用浏览器面板（metacubexd 等）指向手机 IP 才需要改成 true，')
    A('  # 同时把上面的 allow-origins 填成面板的具体地址，不要用 *。')
    A('  allow-private-network: false')
    A('')
    A('# GeoIP 数据库默认从 GitHub raw 拉，17 MB 直连几乎必定超时，')
    A('# 表现是 [GEO] can.t download GeoIP database file: context deadline exceeded，')
    A('# 而且不影响启动——规则静默按旧库匹配。改用 jsDelivr 镜像。')
    A('# 注意 ASN 的文件名是 GeoLite2-ASN.mmdb，写成 asn.mmdb 会 404。')
    A('geo-auto-update: true')
    A('geo-update-interval: 24')
    A('geox-url:')
    for k, fn in [('geoip', 'geoip.dat'), ('geosite', 'geosite.dat'),
                  ('mmdb', 'country.mmdb'), ('asn', 'GeoLite2-ASN.mmdb')]:
        A('  %s: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/%s'
          % (k, fn))
    A('')
    A('profile:')
    A('  # 记住手动选择的节点，配置更新后不被重置。')
    A('  store-selected: true')
    A('  store-fake-ip: true')
    A('')

    # ---- sniffer ----
    A('# 域名嗅探：fake-ip 之外的兜底。走 IP 直连的连接靠它还原域名，')
    A('# 否则那些连接只能落到 GEOIP 判定，分流精度掉一大截。')
    A('sniffer:')
    A('  enable: true')
    A('  # 用嗅探到的域名覆盖目的地址，规则才能按域名匹配。')
    A('  override-destination: false')
    A('  sniff:')
    A('    HTTP:')
    A('      ports: [80, 8080-8880]')
    A('      override-destination: true')
    A('    TLS:')
    A('      ports: [443, 8443]')
    A('    QUIC:')
    A('      ports: [443, 8443]')
    A('  skip-domain:')
    A('    - "Mijia Cloud"')
    A('    - "+.push.apple.com"')
    A('')

    # ---- DNS ----
    A('dns:')
    A('  enable: true')
    A('  ipv6: false')
    A('  listen: 0.0.0.0:1053')
    A('  enhanced-mode: fake-ip')
    A('  fake-ip-range: 198.18.0.1/16')
    A('  fake-ip-filter:')
    # 与 Stash 覆写同源：从生成好的 stoverride 里读，保证三份配置一致。
    ov = os.path.join(ROOT, 'config', 'stash.stoverride')
    body = io.open(ov, encoding='utf-8').read()
    blk = body.split('fake-ip-filter:', 1)[1].split('default-nameserver:', 1)[0]
    n = 0
    for line in blk.split(chr(10)):
        s = line.strip()
        if s.startswith('#'):
            A('  %s' % s)
        elif s.startswith('- '):
            A('    %s' % s)
            n += 1
    A('  default-nameserver: [223.5.5.5, 119.29.29.29]')
    A('  nameserver:')
    A('    - https://dns.alidns.com/dns-query')
    A('    - https://doh.pub/dns-query')
    A('  proxy-server-nameserver:')
    A('    - https://dns.alidns.com/dns-query')
    A('  nameserver-policy:')
    A('    "geosite:cn":')
    A('      - https://dns.alidns.com/dns-query')
    A('      - https://doh.pub/dns-query')
    A('')

    # ---- proxy-providers ----
    A('proxy-providers:')
    A('  airport:')
    A('    type: http')
    A('    # ↓↓↓ 换成你的订阅地址（必须是 Clash / mihomo 格式）↓↓↓')
    A('    url: "https://请替换.example/subscribe?flag=clash"')
    A('    path: ./providers/airport.yaml')
    A('    interval: 3600')
    A('    health-check:')
    A('      enable: true')
    A('      url: %s' % TEST_URL)
    A('      interval: 300')
    A('  # 自建 CF 节点（edgetunnel 一类）作第二个来源时取消下面的注释。')
    A('  # 节点名须含独立的 CF 或 Cloudflare 才会进 ☁️ CF优选。')
    A('  # 该订阅地址含 UUID，等同密码：只写进 *.local.yaml，别提交。')
    A('  # cf:')
    A('  #   type: http')
    A('  #   url: "https://你的部署域名/你的订阅路径"')
    A('  #   path: ./providers/cf.yaml')
    A('  #   interval: 3600')
    A('')

    # ---- proxy-groups ----
    A('proxy-groups:')

    def grp(name, gtype, proxies=None, **kw):
        A('  - name: %s' % q(name))
        A('    type: %s' % gtype)
        for k, v in kw.items():
            A('    %s: %s' % (k.replace('_', '-'), v))
        if proxies:
            A('    proxies:')
            for x in proxies:
                A('      - %s' % q(x))

    protos = [x[0] for x in PROTOCOLS]

    A('  # 自动测速类与手动选择。filter 排除信息类伪节点与自建 CF 节点。')
    grp('♻️ 自动选择', 'url-test', include_all='true',
        filter=q(bs.AUTO_FILTER), url=TEST_URL, interval=300, tolerance=50)
    grp('🔯 故障转移', 'fallback', include_all='true',
        filter=q(bs.AUTO_FILTER), url=TEST_URL, interval=300)
    grp('🔮 负载均衡', 'load-balance', include_all='true',
        filter=q(bs.AUTO_FILTER), url=TEST_URL, interval=300, strategy='consistent-hashing')
    grp('🔧 手动选择', 'select', include_all='true', filter=q(bs.AUTO_FILTER))

    filtered = (list(bs.ATTRS) + list(bs.REGIONS) + list(bs.DIRECTS) + [bs.CF_GROUP])
    A('  # 线路属性、地区、直连线路（地区 × 无中转）、自建 CF。与 Stash 同一份定义')
    A('  # （build-stash.py 的 ATTRS / REGIONS / DIRECTS / CF_GROUP），都过 guard() 排除伪节点。')
    A('  # 英文缩写用逆序环视包裹，否则裸 US 在忽略大小写下会吃掉 Russia / Australia。')
    for name, rex in filtered:
        grp(name, 'url-test', include_all='true', filter=q(bs.guard(rex)),
            url=TEST_URL, interval=300, tolerance=50)

    A('  # 协议分组（Clash 独有）。mihomo 没有 include-type，只能反着排除其余全部类型。')
    A('  # 这些名字是 AdapterType.String() 的形式——group 层比较的是它，')
    A('  # 不是配置里的 type: 值，所以必须写 Shadowsocks 而不是 ss。')
    A('  # exclude-filter 排掉自建 CF 节点与信息类伪节点。')
    for name, keep in PROTOCOLS:
        ex = '|'.join(t for t in ALL_TYPES if t != keep)
        grp(name, 'url-test', include_all='true', exclude_type=q(ex),
            exclude_filter=q('(?i)(' + bs.CF_NODE + '|剩余|剩餘|流量|到期|过期|過期|重置|套餐)'),
            url=TEST_URL, interval=300, tolerance=50)

    # select 组：候选照抄 config/default.conf，policy-select-name 挪到首位。
    # 协议分组是 Clash 独有的，作为附加候选挂在节点选择与 AI 服务上。
    known = ({'♻️ 自动选择', '🔯 故障转移', '🔮 负载均衡', '🔧 手动选择',
              'DIRECT', 'REJECT', 'REJECT-DROP'}
             | {n for n, _ in filtered} | set(protos) | set(bs.sr_select_groups()))
    extras = {'🚀 节点选择': protos, '🤖 AI 服务': protos}
    A('  # 以下 select 组的候选由 tools/build-clash.py 从 config/default.conf 照抄，')
    A('  # Shadowrocket 的 policy-select-name 在这里体现为「排第一」。')
    for name in bs.sr_select_groups():
        grp(name, 'select', bs.derived_candidates(name, known, extras.get(name)))
    A('')

    # ---- rule-providers ----
    A('rule-providers:')
    kinds = {}
    nprov = 0
    for policy, slug in bs.POLICIES:
        for kind in ('domain', 'ipcidr', 'classical'):
            fname = '%s-%s.txt' % (slug, kind)
            if not os.path.exists(os.path.join(ROOT, 'stash', fname)):
                continue
            kinds.setdefault(slug, []).append(kind)
            nprov += 1
            A('  %s-%s:' % (slug, kind))
            A('    type: http')
            A('    behavior: %s' % kind)
            A('    format: text')
            A('    url: %s%s' % (BASE, fname))
            A('    path: ./ruleset/%s' % fname)
            A('    interval: 86400')
            # 直连抓取时 raw.githubusercontent.com 一被污染，RULE-SET 会静默失效，
            # 流量整批掉到兜底规则且不报错。走代理抓更可靠。
            A('    proxy: 🚀 节点选择')
    A('')

    # ---- rules ----
    A('# 规则由 tools/build-clash.py 从 config/default.conf 的 [Rule] 逐行翻译，')
    A('# 顺序与内联规则与 Shadowrocket 完全一致；注释也一并带过来。')
    A('rules:')
    nrules = bs.emit_rules(A, kinds)
    A('')

    with io.open(OUT, 'w', encoding='utf-8', newline=chr(10)) as fh:
        fh.write(chr(10).join(out))
    print('config/clash.yaml  %d 行 / 分组 %d / 规则集 %d / 规则 %d / fake-ip-filter %d 条'
          % (len(out),
             sum(1 for x in out if x.startswith('  - name:')),
             nprov, nrules, n))
    return 0



if __name__ == '__main__':
    sys.exit(main())
