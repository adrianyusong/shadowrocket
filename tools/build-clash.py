#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成完整 mihomo YAML —— 给 Clash for Apple / Clash Verge 用。

与 build-stash.py 的区别：
  Stash 那份是「覆写」(.stoverride)，靠 #!replace 往订阅上打补丁；
  这份是**完整配置**，因为 clash.md 文档明确要求把覆写合并成标准 mihomo YAML
  （"consolidate override files into a clear mihomo YAML configuration"）。

策略组、地区正则、规则集全部从 build-stash.py 导入，避免两份配置漂移。
domain / ipcidr 规则集引用 mrs/ 下的 MRS（tools/build-mrs.py 从 stash/ 转换），
所以顺序是 build-stash.py → build-mrs.py → 本脚本。

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
MRS_BASE = BASE.rsplit('stash/', 1)[0] + 'mrs/'
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
# 2.2.92 加入 Sudoku 才暴露出来的。tools/sync-modules.py 会拉 Clash Core 所基于的
# mihomo 版本（v1.19.33）比对，漏了在 CI 摘要里告警（不阻断同步）。
# EasyTier 从 mihomo v1.19.31 起进了稳定版。Clash for Apple 只在 Mac 上实现它，
# iPhone / iPad / Apple TV 保留节点但拒绝连接。
ALL_TYPES = ['Shadowsocks', 'ShadowsocksR', 'Snell', 'Socks5', 'Http',
             'Vmess', 'Vless', 'Trojan', 'Hysteria', 'Hysteria2',
             'WireGuard', 'Tuic', 'Ssh', 'Mieru', 'AnyTLS', 'Sudoku',
             'Masque', 'TrustTunnel', 'ShadowQuic', 'OpenVPN',
             'Tailscale', 'ZeroTier', 'EasyTier', 'GostRelay']

# 协议分组。Stash 上做不到（exclude-type 被静默忽略，Hy2 会混进 VLESS 组），
# mihomo 的 adapter/outboundgroup/groupbase.go:211 实现了它，所以这里能做。
# 没有 include-type，只能反着排除其余全部类型。
#
# 只列两家机场合并后确实有的协议。筛完为空的组在 mihomo 里只剩 COMPATIBLE（等同
# DIRECT），挂在 🚀 / 🤖 上就是一个标着协议名、实际直连的开关。只装一家时 🔒 ANYTLS
# （只有落云有）会是空的——README 已写明，这时别选它。
# 旧机场只有 vless 与 hysteria2，落云以 anytls 为主（另有 1 个 vless、2 个 hysteria2；
# 它的 5 个 vmess 全是公告伪节点）。有真实 vmess / trojan 节点后把下面两行加回，
# 并用 tools/check-direct.py 核对（它会按节点真实 type 算出每个协议组的成员）。
#   ('🧩 VMESS 节点',  'Vmess'),
#   ('🐴 TROJAN 节点', 'Trojan'),
PROTOCOLS = [
    ('🔐 VLESS 节点',  'Vless'),
    ('⚡ HY2 节点',    'Hysteria2'),
    ('🔒 ANYTLS 节点', 'AnyTLS'),
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
    A('# 字段按 clash.md（Clash Core v1.19.33-clash.4，基于 mihomo 1.19.33）的说明取舍。')
    A('#')
    A('# ============================================================')
    A('# 这是模板，不是可直接订阅的地址。')
    A('#')
    A('# 下面 proxy-providers 的 url 是占位符，直接订阅本文件会得到')
    A('# 「DNS lookup failed / Provider airport」——占位域名本就解析不了。')
    A('#')
    A('# Clash for Apple：')
    A('#   1. 下载本文件，在配置库的「添加配置」里选「导入文件」')
    A('#   2. 点这份配置的 ⓘ，拉到最下面「编辑源码」，把 proxy-providers.airport.url')
    A('#      换成你的订阅地址（保留引号）；两家机场都用时，再取消 airport-b 的注释')
    A('#      配置大到不能编辑时那里会变成「查看源码」，就先在电脑上改好再导入')
    A('# Clash Verge 直接改本地文件即可。')
    A('#')
    A('# 订阅地址等同凭据：本地改过的文件存成 *.local.yaml（已在 .gitignore 排除），别提交。')
    A('# ============================================================')
    A('#')
    A('# 这份配置只管节点、分组、DNS 与规则。URL 重写去广告、iRingo 这类要解密 HTTPS')
    A('# 的功能，在 Clash for Apple 里用「模块」：配置库 ›「模块」粘贴')
    A('# module/adblock-rewrite.sgmodule 或 iRingo 的 .sgmodule 链接，再按提示安装并')
    A('# 信任「Clash 模块证书」。Apple TV 不支持模块。')
    A('')

    # ---- 基础 ----
    # 不写 mixed-port / allow-lan / find-process-mode：
    #   - 本地代理端口在 clash.md 的字段表里是 Advanced：Packet Tunnel 收全部流量，
    #     正常使用用不到它；Clash Verge 的端口与局域网共享归 GUI 管，写了也会被盖掉。
    #   - find-process-mode 在 iOS / tvOS 固定关闭；macOS 默认 strict，只在有进程规则时才查。
    #     写 off 只会让 Mac 上日后加的进程规则静默失效。
    # 不要再写 global-client-fingerprint：mihomo v1.19 已移除它，内核只记一行
    # level=error、配置不生效。要指纹只能在节点上设 client-fingerprint。
    A('mode: rule')
    A('log-level: info')
    A('ipv6: false')
    A('# 统一延迟基准：扣掉握手耗时再比较，否则 Hy2 会因为握手快而虚高。')
    A('# Clash for Apple 不写也默认开启；写明是给默认关闭的其他 mihomo 客户端。')
    A('unified-delay: true')
    A('tcp-concurrent: true')
    A('keep-alive-interval: 30')
    A('')
    A('# 外部控制器不写在这里：这是公开模板，照抄的人不改就等于把控制 API 开到局域网。')
    A('# 要从电脑看连接与日志，用 App 自带的：更多 › 客户端设置 › 外部控制器 →「局域网」')
    A('# （全程 HTTPS，密钥与证书由 App 生成），再用 tools/tail-clash.py 连，')
    A('# 页面上显示的证书指纹填进 --fingerprint。')
    A('')
    A('# 以下地理库设置只对 Clash Verge 等桌面 mihomo 客户端生效。Clash for Apple 的')
    A('# 地理库由 App 统一准备（iOS / tvOS 用预编译资源），这几项在那里不起作用。')
    A('#')
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
    # 端口与 skip-domain 照 clash.md 的示例。HTTP 原先是 8080-8880：嗅探先等客户端
    # 发第一个字节（最多 1 秒），那一段里服务端先说话的协议每条连接都白等，
    # 同一目标失败 6 次后才被跳过（component/sniffer/dispatcher.go）。
    A('# 域名嗅探：只补「没有域名」的连接。走 fake-ip 的连接自带 DNS 查询时的域名，')
    A('# 嗅探不会碰它们；用得上的是自带 DoH 的浏览器、用 HTTPDNS 的国内 App、')
    A('# 写死 IP 的连接——没有它，这些连接只能落到 GEOIP 判定。')
    A('sniffer:')
    A('  enable: true')
    A('  # mihomo 默认就是 true，写明是与 clash.md 的示例对齐。')
    A('  parse-pure-ip: true')
    A('  # 嗅探到的域名只用于规则匹配，不改写连接目标。域名规则读的是嗅探结果')
    A('  # （Metadata.RuleHost），与这个开关无关；改写目标反而会丢掉 App 自己')
    A('  # 选好的 IP（HTTPDNS），Host 头与 IP 对不上时还会连错地方。')
    A('  override-destination: false')
    A('  sniff:')
    A('    HTTP:')
    A('      ports: [80, 8080]')
    A('    TLS:')
    A('      ports: [443, 8443]')
    A('    QUIC:')
    A('      ports: [443]')
    A('  skip-domain:')
    A('    - "+.lan"')
    A('    - "+.local"')
    A('    - "Mijia Cloud"')
    A('    - "+.push.apple.com"')
    A('')

    # ---- DNS ----
    # 不写 dns.listen：它另开一个 TCP/UDP DNS 服务，代理认证、allow-lan、控制器 secret
    # 都不保护它（clash.md「配置与访问保护」）；Packet Tunnel 与 TUN 的 dns-hijack 也
    # 用不到它。原先的 0.0.0.0:1053 等于对局域网开放解析，check-config.py 现在会拦。
    A('dns:')
    A('  enable: true')
    A('  ipv6: false')
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
    A('    - https://dns.adguard-dns.com/dns-query')
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
    A('  # 第二家机场（例如落云）。分组都是 include-all，加进来就会按节点名自动归组；')
    A('  # 🇰🇷 韩国 / 🔒 ANYTLS 等只有它有节点，只装一家时这些组是空的（空组等同直连）。')
    A('  # airport-b:')
    A('  #   type: http')
    A('  #   url: "https://第二家机场的订阅地址?flag=clash"')
    A('  #   path: ./providers/airport-b.yaml')
    A('  #   interval: 3600')
    A('  #   health-check:')
    A('  #     enable: true')
    A('  #     url: %s' % TEST_URL)
    A('  #     interval: 300')
    A('  # 自建 CF 节点（edgetunnel 一类）作另一个来源时取消下面的注释。')
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
            exclude_filter=q('(?i)(' + bs.CF_NODE + '|' + bs.PSEUDO_WORDS + ')'),
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
    A('# domain / ipcidr 用 MRS（mihomo 的预编译格式，tools/build-mrs.py 从 stash/ 转换）：')
    A('# iOS 的 Network Extension 只有约 50 MiB 可用，大型 text 规则集在设备上解析建表')
    A('# 可能把它挤爆。classical 没有 MRS 格式，仍用 text。')
    A('rule-providers:')
    kinds = {}
    nprov = nmrs = 0
    for policy, slug in bs.POLICIES:
        for kind in ('domain', 'ipcidr', 'classical'):
            fname = '%s-%s.txt' % (slug, kind)
            if not os.path.exists(os.path.join(ROOT, 'stash', fname)):
                continue
            name = '%s-%s' % (slug, kind)
            if kind == 'classical':
                fmt, url, cached = 'text', BASE + fname, fname
            else:
                mrs = name + '.mrs'
                if not os.path.exists(os.path.join(ROOT, 'mrs', mrs)):
                    raise SystemExit('缺少 mrs/%s，先运行 tools/build-mrs.py' % mrs)
                fmt, url, cached = 'mrs', MRS_BASE + mrs, mrs
                nmrs += 1
            kinds.setdefault(slug, []).append(kind)
            nprov += 1
            A('  %s:' % name)
            A('    type: http')
            A('    behavior: %s' % kind)
            A('    format: %s' % fmt)
            A('    url: %s' % url)
            A('    path: ./ruleset/%s' % cached)
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
    print('config/clash.yaml  %d 行 / 分组 %d / 规则集 %d（MRS %d）/ 规则 %d / fake-ip-filter %d 条'
          % (len(out),
             sum(1 for x in out if x.startswith('  - name:')),
             nprov, nmrs, nrules, n))
    return 0



if __name__ == '__main__':
    sys.exit(main())
