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
ALL_TYPES = ['Shadowsocks', 'ShadowsocksR', 'Snell', 'Socks5', 'Http',
             'Vmess', 'Vless', 'Trojan', 'Hysteria', 'Hysteria2',
             'WireGuard', 'Tuic', 'Ssh', 'Mieru', 'AnyTLS']

# 协议分组。Stash 上做不到（exclude-type 被静默忽略，Hy2 会混进 VLESS 组），
# mihomo 的 adapter/outboundgroup/groupbase.go:211 实现了它，所以这里能做。
# 没有 include-type，只能反着排除其余全部类型。
PROTOCOLS = [
    ('🔐 VLESS 节点',  'Vless'),
    ('⚡ HY2 节点',    'Hysteria2'),
    ('🧩 VMESS 节点',  'Vmess'),
    ('🐴 TROJAN 节点', 'Trojan'),
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
    A('# 【必须先做】把下面 proxy-providers 里的订阅地址换成你自己的，')
    A('# 另存为 *.local.yaml（该模式已在 .gitignore 排除）。订阅地址等同凭据。')
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

    regions = [r[0] for r in bs.REGIONS]
    attrs = [a[0] for a in bs.ATTRS]
    protos = [x[0] for x in PROTOCOLS]

    A('  # 总入口。手动选择排最前，其余按维度铺开。')
    grp('🚀 节点选择', 'select',
        ['♻️ 自动选择', '🔧 手动选择'] + regions + attrs + protos + ['DIRECT'])
    grp('🔧 手动选择', 'select', include_all='true')
    grp('♻️ 自动选择', 'url-test', include_all='true',
        filter=q(bs.AUTO_FILTER), url=TEST_URL, interval=300, tolerance=50)

    A('  # 地区分组：按节点名正则筛。英文缩写用逆序环视包裹，')
    A('  # 否则裸 US 在忽略大小写下会吃掉 Russia / Australia / Brussels。')
    for name, rex in bs.REGIONS:
        grp(name, 'url-test', include_all='true', filter=q(rex),
            url=TEST_URL, interval=300, tolerance=50)

    A('  # 线路属性分组，与地区维度正交。')
    for name, rex in bs.ATTRS:
        grp(name, 'url-test', include_all='true', filter=q(rex),
            url=TEST_URL, interval=300, tolerance=50)

    A('  # 协议分组。mihomo 没有 include-type，只能反着排除其余全部类型。')
    A('  # 这些名字是 AdapterType.String() 的形式——group 层比较的是它，')
    A('  # 不是配置里的 type: 值，所以必须写 Shadowsocks 而不是 ss。')
    for name, keep in PROTOCOLS:
        ex = '|'.join(t for t in ALL_TYPES if t != keep)
        grp(name, 'url-test', include_all='true', exclude_type=q(ex),
            url=TEST_URL, interval=300, tolerance=50)

    A('  # 业务分组。候选顺序即默认优先级。')
    common = ['🚀 节点选择', '♻️ 自动选择', '🔧 手动选择'] + regions + ['DIRECT']
    # 预置已定义的组：POLICIES 里的 proxy -> 🚀 节点选择 与上面的总入口同名，
    # 不排掉会生成两个同名分组。
    seen = {'🚀 节点选择', '🔧 手动选择', '♻️ 自动选择'}
    for policy, _slug in bs.POLICIES:
        if policy in seen or policy == 'DIRECT':
            continue
        seen.add(policy)
        if policy in ('🛑 广告拦截', '🍃 应用净化'):
            grp(policy, 'select', ['REJECT', 'DIRECT'])
        elif policy in ('🎯 全球直连', '🇨🇳 国内服务', '🌏 国内媒体'):
            grp(policy, 'select', ['DIRECT', '🚀 节点选择'])
        elif policy == '💰 支付服务':
            A('  # 支付默认直连：走机房 IP 正是风控最敏感的特征。')
            grp(policy, 'select', ['DIRECT', '🚀 节点选择'] + regions)
        elif policy == '🤖 AI 服务':
            A('  # AI 组不含 🚀 节点选择：避免落到自动测速上每请求换出口，')
            A('  # 出口频繁跳变会被判为异常。协议分组一并列为候选。')
            grp(policy, 'select',
                ['🏠 住宅IP', '🛣️ 专线', '🔧 手动选择'] + protos +
                ['🇺🇲 美国', '🇯🇵 日本', '🇸🇬 狮城', '🇬🇧 英国', 'DIRECT'])
        else:
            grp(policy, 'select', common)
    A('  # 兜底：没有任何规则命中的流量。')
    grp('🐟 漏网之鱼', 'select', common)
    A('')

    # ---- rule-providers ----
    A('rule-providers:')
    providers = []
    for policy, slug in bs.POLICIES:
        for kind in ('domain', 'ipcidr', 'classical'):
            fname = '%s-%s.txt' % (slug, kind)
            if not os.path.exists(os.path.join(ROOT, 'stash', fname)):
                continue
            pname = '%s-%s' % (slug, kind)
            providers.append((pname, policy, kind))
            A('  %s:' % pname)
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
    A('rules:')
    A('  # 高频埋点用 REJECT-DROP：静默丢包让 App 等超时才重试。')
    for d in ['rmonitor.qq.com', 'h.trace.qq.com']:
        A('  - DOMAIN,%s,REJECT-DROP' % d)
    for d in ['jpush.cn', 'jpush.io', 'pangolin-sdk-toutiao1.com', 'pangle.io',
              'iadsdk.apple.com']:
        A('  - DOMAIN-SUFFIX,%s,REJECT-DROP' % d)
    A('  # LinkedIn 中国已停运，国内 DNS 仍把它解析到国内 IP，')
    A('  # 不显式指定会被后面的 GEOIP,CN 判成国内而直连。')
    for d in ['linkedin.com', 'licdn.com', 'linkedin-ei.com', 'linkedin.cn',
              'licdn.cn']:
        A('  - DOMAIN-SUFFIX,%s,🚀 节点选择' % d)
    for d in ['poe.com', 'huggingface.co', 'hf.co', 'cursor.sh', 'cursor.com',
              'midjourney.com']:
        A('  - DOMAIN-SUFFIX,%s,🤖 AI 服务' % d)
    A('  # DigiCert 是通用 CA，走代理会给每次 TLS 握手多加一跳。')
    for d in ['digicert.com', 'digicert-validation.com']:
        A('  - DOMAIN-SUFFIX,%s,🎯 全球直连' % d)
    A('  # FCM 推送端点走代理。上游 GoogleFCM 集把它们归为 DIRECT，那是境外环境的')
    A('  # 惯例（长连接过代理更不稳）；但在国内 mtalk.google.com 是通不了的，')
    A('  # 直连等于完全收不到推送。clash-verge/README.md 记录了设备上的实测结果。')
    A('  # 注意 sources.txt 里「FCM 走代理收不到推送」那条注释与此相反，已一并更正。')
    A('  # statsigapi.net 用 REJECT-DROP：主动拒绝会让客户端毫秒级重试，')
    A('  # 静默丢包让它等超时，与 rmonitor.qq.com 是同一类处理。')
    for d in ['mtalk.google.com', 'mtalk-dev.google.com', 'mtalk-staging.google.com', 'alt1-mtalk.google.com', 'alt2-mtalk.google.com', 'alt3-mtalk.google.com', 'alt4-mtalk.google.com', 'alt5-mtalk.google.com', 'alt6-mtalk.google.com', 'alt7-mtalk.google.com', 'alt8-mtalk.google.com']:
        A('  - DOMAIN,%s,📢 谷歌服务' % d)
    A('  - DOMAIN-SUFFIX,statsigapi.net,REJECT-DROP')
    A('  # 联网检测、局域网设备、NTP 校时必须直连。')
    for d in ['msftconnecttest.com', 'msftncsi.com', 'ipv6.microsoft.com',
              'router.asus.com', 'linksys.com', 'linksyssmartwifi.com',
              'belkin.com', 'pool.ntp.org', 'ntp.org.cn', 'time.edu.cn']:
        A('  - DOMAIN-SUFFIX,%s,🎯 全球直连' % d)
    A('  # 中国区地图服务器直连，走代理会让地图数据错乱。')
    for d in ['gspe11-2-cn-ssl.ls.apple.com', 'gspe12-cn-ssl.ls.apple.com',
              'gspe19-cn-ssl.ls.apple.com', 'gspe19-2-cn-ssl.ls.apple.com',
              'gspe79-cn-ssl.ls.apple.com']:
        A('  - DOMAIN,%s,🎯 全球直连' % d)
    A('  - DOMAIN-SUFFIX,is.autonavi.com,🎯 全球直连')
    A('  # 游戏本体下载走直连，否则几十 GB 烧机场套餐。')
    for d in ['steampipe.akamaized.net', 'steampipe-kr.akamaized.net',
              'steampipe-partner.akamaized.net', 'steamcdn-a.akamaihd.net',
              'steamusercontent-a.akamaihd.net', 'steamcontent.tnkjmec.com',
              'blzddist1-a.akamaihd.net', 'blzddistkr1-a.akamaihd.net',
              'blzmedia-a.akamaihd.net', 'blznav.akamaized.net',
              'blizzcon-a.akamaihd.net', 'blz-contentstack.com', 'eac-cdn.com']:
        A('  - DOMAIN-SUFFIX,%s,🎯 全球直连' % d)
    for d in ['pinduoduo.com', 'pinduoduo.net', 'pddpic.com', 'yangkeduo.com']:
        A('  - DOMAIN-SUFFIX,%s,🇨🇳 国内服务' % d)
    A('  - DOMAIN-SUFFIX,cn,🇨🇳 国内服务')
    A('  # 全部 Apple 流量走代理。')
    for d in ['apple.com', 'apple.news', 'aaplimg.com', 'icloud.com',
              'icloud-content.com', 'cdn-apple.com', 'mzstatic.com',
              'apple-cloudkit.com', 'apple-mapkit.com', 'itunes.com', 'me.com']:
        A('  - DOMAIN-SUFFIX,%s,🍎 苹果服务' % d)
    A('  - IP-CIDR,17.0.0.0/8,🍎 苹果服务,no-resolve')
    A('')
    A('  # 规则集')
    for pname, policy, kind in providers:
        suffix = ',no-resolve' if kind == 'ipcidr' else ''
        A('  - RULE-SET,%s,%s%s' % (pname, policy, suffix))
    A('  - GEOIP,CN,🇨🇳 国内服务,no-resolve')
    A('  - MATCH,🐟 漏网之鱼')
    A('')

    with io.open(OUT, 'w', encoding='utf-8', newline=chr(10)) as fh:
        fh.write(chr(10).join(out))
    print('config/clash.yaml  %d 行 / 分组 %d / 规则集 %d / fake-ip-filter %d 条'
          % (len(out),
             sum(1 for x in out if x.startswith('  - name:')),
             len(providers), n))
    return 0



if __name__ == '__main__':
    sys.exit(main())
