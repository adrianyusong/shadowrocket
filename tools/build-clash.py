#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成完整 mihomo YAML —— 给 Clash for Apple / Clash Verge 用。

与 build-stash.py 的区别：
  Stash 那份是「覆写」(.stoverride)，靠 #!replace 往订阅上打补丁；
  这份是**完整配置**，因为 clash.md 文档明确要求把覆写合并成标准 mihomo YAML
  （"consolidate override files into a clear mihomo YAML configuration"）。

策略组、地区正则、测速参数、伪节点排除词全部从 build-stash.py 导入，避免两份配置漂移。
domain / ipcidr 规则集引用 mrs/ 下的 MRS（tools/build-mrs.py 从 stash/ 转换），
所以顺序是 build-stash.py → build-mrs.py → 本脚本。

规则仍由 default.conf 的 [Rule] 逐行翻译。Clash 独有的只有三处，都在本文件里
显式声明，check-config.py 的 check_derived_parity 逐项核对、不许多出别的：
  * QUIC_ENTRY     开头的子规则入口，把 UDP/443 交给 sub-rules 里的镜像规则；
  * GEOSITE_RULE   GEOIP,CN 之前的域名兜底；
  * 只含 USER-AGENT 的 classical 集不引用（内核一条都不认，见 classical_payload）。

协议分组在这里是可行的（Stash 上不行）——见 PROTOCOLS 处的注释。
"""
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'config', 'clash.yaml')
STASH_DIR = os.path.join(ROOT, 'stash')
MRS_DIR = os.path.join(ROOT, 'mrs')

# 从 build-stash.py 取常量：策略映射、地区正则、直连线路只有一份定义。
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

# 内置出站。它们不是节点协议，上面那份清单也就一直没收——但 mihomo 的
# adapter/parser.go 允许在 proxies 里用 type: direct / dns / reject / rematch 声明
# 它们（clash.md「内置出站」也这么写），订阅里带一个这样的条目，include-all 就会把它
# 收进来。直连伪节点延迟最低，进了 url-test 组会被选中，整组流量悄悄直连——与
# default.conf 用「直连 / DIRECT」按名字排除的是同一种东西，这里按类型排，不看名字。
# 所有 include-all 的组都带上；名字同样取自 AdapterType.String()，
# tools/sync-modules.py 的 NON_PROXY_TYPES 是同一批（那边多一个兜底的 Unknown）。
BUILTIN_TYPES = ['Direct', 'Reject', 'RejectDrop', 'Compatible', 'Pass',
                 'PassRule', 'Rematch', 'Dns']

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

# ---- Clash 独有的规则 ----
# QUIC 屏蔽。Shadowrocket 用 [General] 的 block-quic = all-proxy 只挡走代理的 UDP/443；
# Clash for Apple 没有对应开关（clash.md 全站只有「写规则」这一种做法：路由规则页与
# 子规则页的示例都是拒绝 UDP/443）。一条全局的 AND,((NETWORK,UDP),(DST-PORT,443)),REJECT
# 会殃及国内直连——default.conf 当初弃用它正是这个原因。所以把 UDP/443 交给一份
# 子规则：它是主规则的逐条镜像，默认走代理的策略换成 REJECT，默认直连 / 拦截的照旧。
QUIC_SUBRULE = 'QUIC'
QUIC_ENTRY = 'SUB-RULE,(AND,((NETWORK,UDP),(DST-PORT,443))),' + QUIC_SUBRULE
# 镜像里保持原策略的条件：策略本身、或策略组的默认候选（第一项）是这三者之一。
KEEP_POLICIES = ('DIRECT', 'REJECT', 'REJECT-DROP')

# 域名层的最后兜底，插在 GEOIP,CN 那一段之前。见 clash_rule_lines 里写进产物的注释。
GEOSITE_RULE = 'GEOSITE,CN,🇨🇳 国内服务'
GEOSITE_BEFORE = 'GEOIP,CN,🇨🇳 国内服务,no-resolve'

# classical 规则集里 mihomo 认不得的类型。Stash 认 USER-AGENT，所以它们留在共用的
# stash/*-classical.txt 里；Clash 这份内嵌时剔掉，否则每次载入都是一行告警。
UNSUPPORTED_CLASSICAL = ('USER-AGENT',)


def q(s):
    """YAML 双引号字符串。正则里的反斜杠必须转义。

    不转义的话 YAML 会把 \\. 当成转义序列而报 unknown escape character。
    与 build-stash.py 的 q() 保持一致。
    """
    return '"%s"' % s.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')


def classical_payload(slug):
    """stash/<slug>-classical.txt 里 mihomo 认得的规则行，原样、按原顺序。"""
    path = os.path.join(STASH_DIR, '%s-classical.txt' % slug)
    out = []
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        if ln.split(',', 1)[0].strip() in UNSUPPORTED_CLASSICAL:
            continue
        out.append(ln)
    return out


def clash_kinds():
    """slug -> Clash 实际引用的 behavior 列表，供 emit_rules 展开 RULE-SET。

    与 Stash 的差别只有一处：classical 剔掉 USER-AGENT 后一条不剩的（目前是
    apple-media / bahamut / disney / hbo）整份不引用。那几条 RULE-SET 在 mihomo 上
    本来就什么都匹配不到，留着只是多一个空规则集。
    """
    kinds = {}
    for _policy, slug in bs.POLICIES:
        for kind in ('domain', 'ipcidr', 'classical'):
            if not os.path.exists(os.path.join(STASH_DIR, '%s-%s.txt' % (slug, kind))):
                continue
            kinds.setdefault(slug, [])
            if kind == 'classical' and not classical_payload(slug):
                continue
            kinds[slug].append(kind)
    return kinds


def split_policy(rule):
    """把一条规则拆成 (策略之前的各段, 策略, 尾部修饰符)。"""
    parts = [x.strip() for x in rule.split(',')]
    mods = []
    while len(parts) > 2 and parts[-1] == 'no-resolve':
        mods.insert(0, parts.pop())
    return parts[:-1], parts[-1], mods


def quic_mirror(rules, first):
    """主规则 -> QUIC 子规则。first: 策略组名 -> 它的默认候选（第一项）。"""
    out = []
    for r in rules:
        if r == QUIC_ENTRY:
            continue
        head, pol, mods = split_policy(r)
        keep = pol in KEEP_POLICIES or first.get(pol) in KEEP_POLICIES
        out.append(','.join(head + [pol if keep else 'REJECT'] + mods))
    return out


def clash_rule_lines(kinds):
    """rules: 段的输出行（含注释）：default.conf 的翻译，加 Clash 独有的两条。"""
    lines = []
    bs.emit_rules(lines.append, kinds)

    anchor = '  - ' + GEOSITE_BEFORE
    if lines.count(anchor) != 1:
        raise SystemExit('default.conf 的 [Rule] 里找不到唯一的 %s，GEOSITE,CN 无处可插'
                         % GEOSITE_BEFORE)
    i = lines.index(anchor)
    while i > 0 and lines[i - 1].startswith('  #'):      # 插在 GEOIP 那段注释之前
        i -= 1
    lines[i:i] = [
        '  # GEOSITE,CN（Clash 独有）：域名层的最后兜底。上面的列表没覆盖到的国内域名原本',
        '  # 会落到 MATCH 走代理，只能发现一个往 rule/ChinaDomains.list 补一个。clash.md',
        '  # 「配置最佳实践」的社区模板直接用 GEOSITE,CN；iOS / tvOS 上它是 App 备好的预编译资源。',
        '  # 排在 proxy 规则集之后：只改变「本来会落到兜底」的域名的去向，不与任何显式规则抢。',
        '  # 代价：这部分域名在 Clash 上直连，在 Shadowrocket / Stash 上仍走代理——',
        '  # 三份配置唯一一处有意的分流差异。',
        '  - ' + GEOSITE_RULE,
    ]

    head = [
        '  # ============ QUIC（Clash 独有）============',
        '  # Shadowrocket 用 block-quic = all-proxy 只挡走代理的 UDP/443，Clash for Apple 没有',
        '  # 这个开关，clash.md 的做法是写规则。UDP/443 一律先进子规则 %s（文件末尾的' % QUIC_SUBRULE,
        '  # sub-rules）：它是下面全部规则的镜像，默认走代理的换成 REJECT，默认直连的照旧。',
        '  # 所以国内直连的 QUIC 不受影响，代理流量回落 TCP。必须是第一条。',
        '  - ' + QUIC_ENTRY,
    ]
    return head + lines


def rules_of(lines):
    return [x[4:] for x in lines if x.startswith('  - ')]


def main():
    # 同 build-stash.py：Windows 主控台默认 cp1252，印中文会抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    out = []
    A = out.append

    A('# Clash for Apple / Clash Verge 完整配置')
    A('# 由 tools/build-clash.py 生成，请勿手改——下次同步会覆盖。')
    A('# https://github.com/adrianyusong/shadowrocket')
    A('# 字段按 clash.md（Clash Core v1.19.33-clash.5，基于 mihomo 1.19.33）的说明取舍。')
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
    A('# 日常用 warning（clash.md「配置最佳实践」）：info 级每条连接记一行。排查时把这里')
    A('# 临时改回 info，或直接用外部控制器的 /logs?level=info 订阅——mihomo 的日志事件是')
    A('# 先发布、再按这里的级别决定要不要落盘，订阅端按请求里的 level 过滤，不受这里影响。')
    A('log-level: warning')
    A('# 写 true 是为了让隧道接管 IPv6，不是为了让 App 用上 IPv6。clash.md「TUN 与网络接入」：')
    A('# ipv6 为 false 时隧道不接管 IPv6——在有 IPv6 的网络（多数蜂窝网络）上，直接用 IPv6')
    A('# 地址发起的连接整个绕过 Clash：不匹配规则、不走代理、不拦截。受影响的是自带解析')
    A('# （HTTPDNS / DoH）或内置 IPv6 地址的 App，例如 Telegram。')
    A('# App 照样拿不到 IPv6 地址：下面 dns.ipv6 保持 false，不回 AAAA。')
    A('# 副作用：Clash 自己直连时可能用 IPv6（与不开代理时系统的行为一致）；连节点仍优先')
    A('# IPv4，见 proxy-providers 的 override.ip-version。')
    A('ipv6: true')
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
    A('# 规则里用到地理库的有三处：GEOIP,CN、GEOSITE,CN，以及 classical 集里的 IP-ASN')
    A('# （ai / telegram / meta / china 共 9 条）。ASN 库在 Clash for Apple 上不会由隧道')
    A('# 自动下载——到 App 的「资源」页确认它已备好，否则那几条 IP-ASN 不生效。')
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
    A('  # 不给 App 返回 IPv6 地址（AAAA）。顶层 ipv6 为 true 只是让隧道接管 IPv6，见上。')
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
    # 这一段与 Shadowrocket / Stash 不同：那两份仍并行查 AdGuard，并在各自的注释里写明
    # 是「抢到才生效」的补充拦截。Clash 这份按 clash.md「只在确实需要时添加自定义 DNS」去掉。
    A('  # fake-ip 加全部 IP 规则带 no-resolve 之下，只有走直连的域名才会真的在本地解析，')
    A('  # 所以只放国内 DoH。以前还并行查 AdGuard：从大陆它几乎抢不赢，偶尔抢赢时返回的是')
    A('  # 境外视角的 CDN 地址或被它答空，行为不确定；广告拦截交给规则集。')
    A('  # 原先的 nameserver-policy（geosite:cn）与这组解析器完全相同，一并去掉。')
    A('  nameserver:')
    A('    - https://dns.alidns.com/dns-query')
    A('    - https://doh.pub/dns-query')
    A('  # 解析节点域名。两家并行，免得一家出问题时所有节点都解析不了。')
    A('  proxy-server-nameserver:')
    A('    - https://dns.alidns.com/dns-query')
    A('    - https://doh.pub/dns-query')
    A('')

    # ---- proxy-providers ----
    provider_exclude = q('(?i)(' + bs.PSEUDO_WORDS + ')')
    A('proxy-providers:')
    A('  # 机场订阅共用的三项（取消 airport-b 的注释时同样带上）：')
    A('  #   proxy: DIRECT         订阅更新不经节点。不写时请求按规则走，落到 🐟 漏网之鱼 → 🚀，')
    A('  #                         等于用这家机场的节点去更新这家机场的订阅：节点全挂（换 IP、')
    A('  #                         到期续费后）就再也更新不了。clash.md 也提醒别依赖集合自身。')
    A('  #                         订阅域名在当前网络直连不通时，改成另一家订阅的节点或组名。')
    A('  #   exclude-filter        信息类伪节点在这里就丢掉，不进任何分组。各组的 filter 仍带')
    A('  #                         同一批排除词作第二道保险（新加的订阅忘了写这一项时靠它）。')
    A('  #   override.ip-version   连节点优先 IPv4。顶层 ipv6 为 true 之后，节点域名有 AAAA 时')
    A('  #                         内核会两个地址族一起试；多数机场的 IPv6 入口不完整，照旧走 IPv4。')
    A('  airport:')
    A('    type: http')
    A('    # ↓↓↓ 换成你的订阅地址（必须是 Clash / mihomo 格式）↓↓↓')
    A('    url: "https://请替换.example/subscribe?flag=clash"')
    A('    path: ./providers/airport.yaml')
    A('    interval: 3600')
    A('    proxy: DIRECT')
    A('    exclude-filter: %s' % provider_exclude)
    A('    override:')
    A('      ip-version: ipv4-prefer')
    A('    health-check:')
    A('      enable: true')
    A('      url: %s' % TEST_URL)
    A('      interval: 300')
    A('  # 第二家机场（例如落云）。分组都是 include-all，加进来就会按节点名自动归组；')
    A('  # 🇰🇷 韩国直连 / 🔒 ANYTLS 等只有它有节点，只装一家时这些组是空的（空组等同直连）。')
    A('  # airport-b:')
    A('  #   type: http')
    A('  #   url: "https://第二家机场的订阅地址?flag=clash"')
    A('  #   path: ./providers/airport-b.yaml')
    A('  #   interval: 3600')
    A('  #   proxy: DIRECT')
    A('  #   exclude-filter: %s' % provider_exclude)
    A('  #   override:')
    A('  #     ip-version: ipv4-prefer')
    A('  #     # 给这家的节点名加前缀，两家有同名节点时才分得清（clash.md「配置最佳实践」的写法）。')
    A('  #     # 前缀不含地区缩写，不影响各组按名字归类。')
    A('  #     additional-prefix: "[B] "')
    A('  #   health-check:')
    A('  #     enable: true')
    A('  #     url: %s' % TEST_URL)
    A('  #     interval: 300')
    A('  # 自建 CF 节点（edgetunnel 一类）作另一个来源时取消下面的注释。')
    A('  # 节点名须含独立的 CF 或 Cloudflare 才会进 ☁️ CF优选。')
    A('  # 该订阅地址含 UUID，等同密码：只写进 *.local.yaml，别提交。')
    A('  # 不写 proxy: DIRECT：workers.dev / pages.dev 一类部署域名在大陆常被污染，')
    A('  # 它的订阅照旧按规则经机场节点更新，不存在依赖自身的问题。')
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
    builtin = q('|'.join(BUILTIN_TYPES))
    auto_iv, auto_tol = bs.AUTO_TEST
    region_iv, region_tol = bs.REGION_TEST

    A('  # 自动测速类与手动选择。filter 排除信息类伪节点与自建 CF 节点。')
    A('  # exclude-type 排掉订阅里用 type: direct / dns / reject / rematch 声明的内置出站——')
    A('  # 它们不是节点；直连伪节点延迟最低，混进 url-test 组会被选中，整组流量悄悄直连。')
    A('  # 下面每个 include-all 的组都带这一项。测速参数与 Shadowrocket / Stash 是同一组数值。')
    grp('♻️ 自动选择', 'url-test', include_all='true', filter=q(bs.AUTO_FILTER),
        exclude_type=builtin, url=TEST_URL, interval=auto_iv, tolerance=auto_tol)
    grp('🔯 故障转移', 'fallback', include_all='true', filter=q(bs.AUTO_FILTER),
        exclude_type=builtin, url=TEST_URL, interval=auto_iv)
    grp('🔮 负载均衡', 'load-balance', include_all='true', filter=q(bs.AUTO_FILTER),
        exclude_type=builtin, url=TEST_URL, interval=auto_iv, strategy='consistent-hashing')
    grp('🔧 手动选择', 'select', include_all='true', filter=q(bs.AUTO_FILTER),
        exclude_type=builtin)

    filtered = list(bs.DIRECTS) + [bs.CF_GROUP]
    A('  # 地区直连线路（地区 × 无中转）、自建 CF。与 Stash 同一份定义')
    A('  # （build-stash.py 的 DIRECTS / CF_GROUP），都过 guard() 排除伪节点。')
    A('  # 英文缩写用逆序环视包裹，否则裸 US 在忽略大小写下会吃掉 Russia / Australia。')
    for name, rex in filtered:
        grp(name, 'url-test', include_all='true', filter=q(bs.guard(rex)),
            exclude_type=builtin, url=TEST_URL, interval=region_iv, tolerance=region_tol)

    A('  # 协议分组（Clash 独有）。mihomo 没有 include-type，只能反着排除其余全部类型。')
    A('  # 这些名字是 AdapterType.String() 的形式——group 层比较的是它，')
    A('  # 不是配置里的 type: 值，所以必须写 Shadowsocks 而不是 ss。')
    A('  # exclude-filter 与自动测速组排除的是同一批名字（伪节点、直连伪节点、自建 CF）。')
    proto_exclude = q('(?i)(' + bs.CF_NODE + '|' + bs.EXCLUDE_INFO + ')')
    for name, keep in PROTOCOLS:
        ex = '|'.join([t for t in ALL_TYPES if t != keep] + BUILTIN_TYPES)
        grp(name, 'url-test', include_all='true', exclude_type=q(ex),
            exclude_filter=proto_exclude,
            url=TEST_URL, interval=region_iv, tolerance=region_tol)

    # select 组：候选照抄 config/default.conf，policy-select-name 挪到首位。
    # 协议分组是 Clash 独有的，作为附加候选挂在节点选择与 AI 服务上。
    known = ({'♻️ 自动选择', '🔯 故障转移', '🔮 负载均衡', '🔧 手动选择',
              'DIRECT', 'REJECT', 'REJECT-DROP'}
             | {n for n, _ in filtered} | set(protos) | set(bs.sr_select_groups()))
    extras = {'🚀 节点选择': protos, '🤖 AI 服务': protos}
    first = {}              # 策略组 -> 默认候选，QUIC 镜像靠它判断「默认走不走代理」
    A('  # 以下 select 组的候选由 tools/build-clash.py 从 config/default.conf 照抄，')
    A('  # Shadowrocket 的 policy-select-name 在这里体现为「排第一」。')
    for name in bs.sr_select_groups():
        cands = bs.derived_candidates(name, known, extras.get(name))
        first[name] = cands[0]
        grp(name, 'select', cands)
    A('')

    # ---- rule-providers ----
    A('# domain / ipcidr 用 MRS（mihomo 的预编译格式，tools/build-mrs.py 从 stash/ 转换）：')
    A('# iOS 的 Network Extension 只有约 50 MiB 可用，大型 text 规则集在设备上解析建表')
    A('# 可能把它挤爆。')
    A('# classical 没有 MRS 格式，而且每份只有几条到一百多条，直接内嵌（type: inline）：')
    A('# 不必为它们各开一个远程规则集，启动与每天更新时少发同样多的请求，也不存在「拉取')
    A('# 失败后规则静默消失」。内嵌时剔掉 mihomo 不认的 USER-AGENT（Stash 认，所以共用的')
    A('# stash/ 源文件里还留着）。代价：这部分规则只随本文件更新，不再每天自己刷新。')
    A('rule-providers:')
    kinds = clash_kinds()
    nprov = nmrs = ninline = npayload = 0
    skipped = []
    for _policy, slug in bs.POLICIES:
        for kind in ('domain', 'ipcidr', 'classical'):
            fname = '%s-%s.txt' % (slug, kind)
            if not os.path.exists(os.path.join(STASH_DIR, fname)):
                continue
            name = '%s-%s' % (slug, kind)
            if kind == 'classical':
                payload = classical_payload(slug)
                if not payload:
                    skipped.append(name)
                    continue
                A('  %s:' % name)
                A('    type: inline')
                A('    behavior: classical')
                A('    payload:')
                for x in payload:
                    A('      - %s' % q(x))
                ninline += 1
                npayload += len(payload)
            else:
                mrs = name + '.mrs'
                if not os.path.exists(os.path.join(MRS_DIR, mrs)):
                    raise SystemExit('缺少 mrs/%s，先运行 tools/build-mrs.py' % mrs)
                A('  %s:' % name)
                A('    type: http')
                A('    behavior: %s' % kind)
                A('    format: mrs')
                A('    url: %s' % (MRS_BASE + mrs))
                A('    path: ./ruleset/%s' % mrs)
                A('    interval: 86400')
                # 直连抓取时 raw.githubusercontent.com 一被污染，RULE-SET 会静默失效，
                # 流量整批掉到兜底规则且不报错。走代理抓更可靠。
                A('    proxy: 🚀 节点选择')
                nmrs += 1
            nprov += 1
    if skipped:
        A('  # 没有引用的 classical 集（剔掉 USER-AGENT 后一条不剩）：%s' % '、'.join(skipped))
    A('')

    # ---- rules ----
    A('# 规则由 tools/build-clash.py 从 config/default.conf 的 [Rule] 逐行翻译，')
    A('# 顺序与内联规则与 Shadowrocket 完全一致；注释也一并带过来。')
    A('# Clash 独有的只有三处：开头的 QUIC 子规则入口、GEOIP,CN 之前的 GEOSITE,CN，')
    A('# 以及不引用上面那几份只含 USER-AGENT 的 classical 集。')
    A('rules:')
    lines = clash_rule_lines(kinds)
    out.extend(lines)
    rules = rules_of(lines)
    A('')

    # ---- sub-rules ----
    mirror = quic_mirror(rules, first)
    A('# QUIC 子规则：上面 rules 的逐条镜像（tools/build-clash.py 生成，条数恒等于 rules 减一）。')
    A('# 策略组的默认候选（第一项）是 DIRECT / REJECT / REJECT-DROP 的保持原策略，其余换成 REJECT。')
    A('# 按默认候选判定，不看你在 App 里的当前选择：把某个代理组手动切成 DIRECT 后，它的')
    A('# QUIC 仍被拒绝、回落 TCP，不影响使用；反过来把直连组切去代理，QUIC 会跟着走代理。')
    A('sub-rules:')
    A('  %s:' % QUIC_SUBRULE)
    for r in mirror:
        A('    - %s' % r)
    A('')

    with io.open(OUT, 'w', encoding='utf-8', newline=chr(10)) as fh:
        fh.write(chr(10).join(out))
    print('config/clash.yaml  %d 行 / 分组 %d / 规则集 %d（MRS %d，内嵌 classical %d 份 %d 条）'
          ' / 规则 %d + QUIC 镜像 %d（其中 REJECT %d）/ fake-ip-filter %d 条'
          % (len(out),
             sum(1 for x in out if x.startswith('  - name:')),
             nprov, nmrs, ninline, npayload, len(rules), len(mirror),
             sum(1 for a, b in zip([r for r in rules if r != QUIC_ENTRY], mirror) if a != b),
             n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
