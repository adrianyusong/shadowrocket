#!/usr/bin/env python3
"""从共用的 rule/*.list 生成 Stash（Clash / Mihomo 内核）配置。

与 Shadowrocket 配置完全分开，但共用同一套规则数据：
    tools/sources.txt -> tools/sync-rules.py -> rule/*.list
                                                  |
                          +-----------------------+----------------------+
                          |                                              |
                config/default.conf                        tools/build-stash.py
                  (Shadowrocket)                                   |
                                                    stash/*.txt + config/stash.stoverride

为什么要拆分规则文件：Stash 的 rule-provider 有三种 behavior，
domain 与 ipcidr 是为海量规则优化过的加载器，classical 虽然什么都能装
但效率最低。我们的 .list 是混合格式，直接当 classical 会白白浪费性能，
所以按类型拆成三份，各走各的加载器。

输出为 .stoverride 覆写文件而非完整配置：节点来自你的订阅，
覆写只替换规则与分组，不碰节点，也就不需要把订阅地址写进仓库。

用法：
    python tools/build-stash.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULEDIR = os.path.join(ROOT, 'rule')
OUTDIR = os.path.join(ROOT, 'stash')
OVERRIDE = os.path.join(ROOT, 'config', 'stash.stoverride')
BASE = 'https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/stash/'

# 排除信息类伪节点（机场把剩余流量、到期时间也做成节点）
EXCLUDE_INFO = ('官网|官方|网站|網站|客服|邀请|邀請|重置|剩余|剩餘|到期|过期|過期|'
                '流量|套餐|订阅|訂閱|群组|群組|直连|直連|Expire|Traffic|Reset|Website')

# 自动测速类分组的 filter：排除上面那些信息类伪节点。
AUTO_FILTER = '(?i)^(?!.*(' + EXCLUDE_INFO + ')).*$'

# Mihomo 的节点类型标识，用于 exclude-type。
# proxy-groups 只有 exclude-type 没有 include-type，所以「只要某协议」
# 得写成「排除其余全部」。
ALL_TYPES = ['ss', 'ssr', 'vmess', 'vless', 'trojan', 'hysteria', 'hysteria2',
             'tuic', 'wireguard', 'snell', 'http', 'socks5', 'anytls', 'ssh']


def only_type(*keep):
    """生成 exclude-type 值：排除 keep 之外的全部协议。"""
    return '|'.join(t for t in ALL_TYPES if t not in keep)


# 地区分组的节点名正则。英文缩写一律用逆序环视包裹——裸 US 在忽略大小写下
# 会匹配 Russia / Australia / Brussels / Plus，把俄罗斯澳洲节点混进美国组。
REGIONS = [
    ('🇭🇰 香港', r'(?i)(香港|港岛|深港|沪港|Hong ?Kong|(?<![A-Za-z])(HK|HKG)(?![A-Za-z]))'),
    ('🇹🇼 台湾', r'(?i)(台湾|台灣|台北|臺灣|Taiwan|(?<![A-Za-z])(TW|TPE)(?![A-Za-z]))'),
    ('🇯🇵 日本', r'(?i)(日本|東京|东京|大阪|名古屋|埼玉|Japan|(?<![A-Za-z])(JP|JPN|NRT|KIX)(?![A-Za-z]))'),
    ('🇸🇬 狮城', r'(?i)(新加坡|狮城|獅城|Singapore|(?<![A-Za-z])(SG|SIN)(?![A-Za-z]))'),
    ('🇺🇲 美国', r'(?i)(美国|美國|美西|美东|美東|洛杉矶|圣何塞|西雅图|达拉斯|凤凰城|United ?States|(?<![A-Za-z])(US|USA|LAX|SJC)(?![A-Za-z]))'),
    ('🇰🇷 韩国', r'(?i)(韩国|韓國|首尔|首爾|Korea|(?<![A-Za-z])(KR|ICN)(?![A-Za-z]))'),
]

# 线路属性分组。取自节点名的实际标签，与地区维度正交。
ATTRS = [
    ('🏠 住宅IP', r'(?i)(住宅|家宽|家寬|原生|Residential)'),
    ('🛣️ 专线', r'(?i)(专线|專線|IPLC|IEPL)'),
    ('🎞️ 流媒体节点', r'(?i)(流媒体|流媒體)'),
    ('💴 低倍率', r'(?i)(?<![0-9.])0\.[0-9]+ ?x'),
]

# 协议分组。Shadowrocket 做不到这个维度——它只能匹配节点名，
# 而协议信息不在名字里。Mihomo 从节点配置读类型，所以能分。
PROTOS = [
    ('🅥 VLESS', only_type('vless')),
    ('🅗 Hysteria2', only_type('hysteria2', 'hysteria')),
    ('🅣 Trojan', only_type('trojan')),
    ('🅢 Shadowsocks', only_type('ss', 'ssr')),
]

TEST_URL = 'http://www.gstatic.com/generate_204'

# 策略 -> (rule/ 里的文件名, 是否为拦截类)
POLICIES = [
    ('DIRECT',        'direct'),
    ('🛑 广告拦截',    'reject-ads'),
    ('🍃 应用净化',    'reject-privacy'),
    ('🍎 苹果服务',    'apple'),
    ('🎵 苹果媒体',    'apple-media'),
    ('🌏 国内媒体',    'media-cn'),
    ('📹 YOUTUBE',    'youtube'),
    ('🎥 NETFLIX',    'netflix'),
    ('🎬 DISNEY+',    'disney'),
    ('🎦 HBO',        'hbo'),
    ('📦 PRIMEVIDEO', 'primevideo'),
    ('🎧 SPOTIFY',    'spotify'),
    ('🕹️ 巴哈姆特',   'bahamut'),
    ('📺 ABEMATV',    'abematv'),
    ('🎙️ TWITCH',    'twitch'),
    ('📀 EMBY',       'emby'),
    ('☁️ PIKPAK',    'pikpak'),
    ('🌍 国外媒体',    'media-global'),
    ('🤖 AI 服务',     'ai'),
    ('📲 TELEGRAM',   'telegram'),
    ('🐦 TWITTER',    'twitter'),
    ('📘 META',       'meta'),
    ('💬 DISCORD',    'discord'),
    ('💚 LINE',       'line'),
    ('💰 支付服务',    'payment'),
    ('🐱 GITHUB',     'github'),
    ('🔍 BING',       'bing'),
    ('Ⓜ️ 微软服务',   'microsoft'),
    ('📢 谷歌服务',    'google'),
    ('🎶 TIKTOK',     'tiktok'),
    ('🎯 全球直连',    'direct-global'),
    ('🎮 游戏平台',    'game'),
    ('🇨🇳 国内服务',   'china'),
    ('🇨🇳 国内服务',   'ChinaDomains'),
    ('🚀 节点选择',    'proxy'),
]

DOMAIN_TYPES = {'DOMAIN', 'DOMAIN-SUFFIX'}
IP_TYPES = {'IP-CIDR', 'IP-CIDR6', 'IP6-CIDR'}


def split_rules(path):
    """把一个 .list 拆成 domain / ipcidr / classical 三份。"""
    dom, ip, cls = [], [], []
    for line in io.open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [x.strip() for x in line.split(',')]
        if len(parts) < 2:
            continue
        rtype, value = parts[0], parts[1]
        if rtype == 'DOMAIN':
            dom.append(value)
        elif rtype == 'DOMAIN-SUFFIX':
            # Clash domain behavior 用 +. 表示后缀（含自身）
            dom.append('+.' + value)
        elif rtype in IP_TYPES:
            ip.append(value)
        else:
            # DOMAIN-KEYWORD / USER-AGENT / PROCESS-NAME 等只能走 classical
            cls.append(','.join(parts))
    return dom, ip, cls


def write_set(name, lines, kind):
    if not lines:
        return None
    path = os.path.join(OUTDIR, '%s-%s.txt' % (name, kind))
    with io.open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('# 由 tools/build-stash.py 从 rule/%s.list 生成，请勿手改。\n' % name)
        fh.write('# behavior: %s   条数: %d\n' % (kind, len(lines)))
        for x in lines:
            fh.write(x + '\n')
    return os.path.basename(path)


def q(s):
    """YAML 双引号字符串。正则里有反斜杠，必须转义。"""
    return '"%s"' % s.replace('\\', '\\\\').replace('"', '\\"')


def main():
    if not os.path.isdir(RULEDIR):
        print('找不到 rule/，请先运行 tools/sync-rules.py')
        return 1
    os.makedirs(OUTDIR, exist_ok=True)

    # 清掉旧产物，避免删掉某个策略后残留文件仍被引用（sync-rules 踩过这个坑）
    for f in os.listdir(OUTDIR):
        if f.endswith('.txt'):
            os.remove(os.path.join(OUTDIR, f))

    providers = []          # (provider_name, behavior, filename)
    rule_lines = []         # 最终 rules: 列表
    stats = {'domain': 0, 'ipcidr': 0, 'classical': 0}

    for policy, slug in POLICIES:
        src = os.path.join(RULEDIR, slug + '.list')
        if not os.path.exists(src):
            print('缺少 rule/%s.list，跳过' % slug)
            continue
        dom, ip, cls = split_rules(src)
        for kind, data in (('domain', dom), ('ipcidr', ip), ('classical', cls)):
            fname = write_set(slug, data, kind)
            if not fname:
                continue
            stats[kind] += len(data)
            pname = '%s-%s' % (slug, kind)
            providers.append((pname, kind, fname))
            # ipcidr 一律加 no-resolve：不加的话每个域名请求都会为了判断 IP
            # 归属而触发一次本地 DNS 查询，既泄漏域名也让污染结果影响分流。
            suffix = ',no-resolve' if kind == 'ipcidr' else ''
            rule_lines.append('  - RULE-SET,%s,%s%s' % (pname, policy, suffix))

    out = []
    A = out.append
    A('# Stash 覆写文件（.stoverride）')
    A('#')
    A('# 由 tools/build-stash.py 生成，请勿手改。')
    A('# 规则数据与 Shadowrocket 配置共用 rule/*.list，两边分开维护各自的配置。')
    A('#')
    A('# 这是覆写而非完整配置：节点来自你自己的订阅，覆写只替换规则与分组，')
    A('# 不碰节点，所以仓库里不需要出现订阅地址。')
    A('#')
    A('# 用法：Stash -> 配置 -> 覆写 -> 添加，填入本文件的 raw 地址，')
    A('# 然后在订阅配置上启用该覆写。')
    A('')
    A('name: Shadowrocket 全量配置 (Stash)')
    A('desc: 与 Shadowrocket 配置共用规则数据。含协议分组、地区分组、线路属性分组。')
    A('author: adrianyusong')
    A('homepage: https://github.com/adrianyusong/shadowrocket')
    A('category: rules')
    A('')

    # ---- DNS ----
    A('# 用 #!replace 整段替换订阅自带的 dns，避免与机场配置相互干扰。')
    A('dns: #!replace')
    A('  enable: true')
    A('  ipv6: false')
    A('  # fake-ip 让域名不必真正解析就能进规则匹配，是避免 DNS 泄漏的关键。')
    A('  enhanced-mode: fake-ip')
    A('  fake-ip-range: 198.18.0.1/16')
    A('  # 这些必须拿到真实 IP，走 fake-ip 会坏：局域网发现、游戏机 STUN、')
    A('  # 系统联网检测。')
    A('  fake-ip-filter:')
    for x in ['"*.lan"', '"*.local"', '"+.srv.nintendo.net"', '"+.stun.playstation.net"',
              '"+.msftconnecttest.com"', '"+.msftncsi.com"', '"localhost.ptlogin2.qq.com"',
              '"+.battle.net"', '"stun.*"', '"time.*.com"', '"ntp.*.com"']:
        A('    - %s' % x)
    A('  default-nameserver:')
    A('    - 223.5.5.5')
    A('    - 119.29.29.29')
    A('  # 直连域名用国内 DoH 解析；明文 UDP 53 本身可被中间人劫持。')
    A('  nameserver:')
    A('    - https://dns.alidns.com/dns-query')
    A('    - https://doh.pub/dns-query')
    A('  # 解析节点域名时用的 DNS。走这里可避免「解析节点地址」这一步本身被污染。')
    A('  proxy-server-nameserver:')
    A('    - https://dns.alidns.com/dns-query')
    A('  # 代理域名交给远端解析，不在本地留痕。')
    A('  nameserver-policy:')
    A('    "geosite:cn":')
    A('      - https://dns.alidns.com/dns-query')
    A('      - https://doh.pub/dns-query')
    A('')

    # ---- proxy-groups ----
    A('# 分组用 #!replace 整段替换，否则会与订阅自带的分组混在一起。')
    A('proxy-groups: #!replace')

    def grp(name, gtype, proxies=None, **kw):
        A('  - name: %s' % q(name))
        A('    type: %s' % gtype)
        for k, v in kw.items():
            A('    %s: %s' % (k.replace('_', '-'), v))
        if proxies:
            A('    proxies:')
            for p in proxies:
                A('      - %s' % q(p))

    main_cands = (['♻️ 自动选择', '🔯 故障转移', '🔮 负载均衡', '🔧 手动选择']
                  + [n for n, _ in PROTOS] + [n for n, _ in ATTRS]
                  + [n for n, _ in REGIONS] + ['DIRECT'])
    A('  # 主策略。候选里同时给出协议、线路属性、地区三个维度，按需切换。')
    grp('🚀 节点选择', 'select', main_cands)

    A('  # 自动测速类。filter 排除机场的信息类伪节点。')
    grp('♻️ 自动选择', 'url-test', None, filter=q(AUTO_FILTER),
        url=q(TEST_URL), interval=300, tolerance=100, lazy='true')
    grp('🔯 故障转移', 'fallback', None, filter=q(AUTO_FILTER),
        url=q(TEST_URL), interval=300, lazy='true')
    grp('🔮 负载均衡', 'load-balance', None, filter=q(AUTO_FILTER),
        url=q(TEST_URL), interval=300, strategy='consistent-hashing')

    A('  # 手动挑单个节点用。上面几组都是自动的，没有这一组就只能选组不能选节点。')
    grp('🔧 手动选择', 'select', None, filter=q(AUTO_FILTER))

    A('  # 协议分组。proxy-groups 只有 exclude-type 没有 include-type，')
    A('  # 所以「只要某协议」写成「排除其余全部」。这是 Shadowrocket 做不到的维度。')
    for name, ex in PROTOS:
        grp(name, 'url-test', None, exclude_type=q(ex),
            url=q(TEST_URL), interval=600, tolerance=200, lazy='true')

    A('  # 线路属性分组，与地区维度正交。')
    for name, f in ATTRS:
        grp(name, 'url-test', None, filter=q(f),
            url=q(TEST_URL), interval=600, tolerance=200, lazy='true')

    A('  # 地区分组。')
    for name, f in REGIONS:
        grp(name, 'url-test', None, filter=q(f),
            url=q(TEST_URL), interval=600, tolerance=200, lazy='true')

    A('  # AI 对 IP 风控极严。住宅 IP 排首位——机房 IP 是判定代理的首要特征。')
    A('  # 候选里刻意不放 🚀 节点选择，避免间接落到负载均衡上每请求换出口。')
    grp('🤖 AI 服务', 'select',
        ['🏠 住宅IP', '🛣️ 专线', '🇺🇲 美国', '🇯🇵 日本', '🇸🇬 狮城', 'DIRECT'])

    A('  # 流媒体对 IP 跳变敏感，机场自标的流媒体节点排首位。')
    for n in ['📹 YOUTUBE', '🎥 NETFLIX', '🎬 DISNEY+', '🎦 HBO', '📦 PRIMEVIDEO']:
        grp(n, 'select', ['🎞️ 流媒体节点', '🏠 住宅IP', '🇸🇬 狮城', '🇭🇰 香港',
                          '🇯🇵 日本', '🇺🇲 美国', '🚀 节点选择', 'DIRECT'])
    for n in ['🎧 SPOTIFY', '🎶 TIKTOK', '🎙️ TWITCH', '☁️ PIKPAK', '🌍 国外媒体',
              '📲 TELEGRAM', '🐦 TWITTER', '📘 META', '💬 DISCORD', '🐱 GITHUB',
              '🔍 BING', '📢 谷歌服务', '🎵 苹果媒体']:
        grp(n, 'select', ['🚀 节点选择', '♻️ 自动选择', 'DIRECT'])
    grp('🕹️ 巴哈姆特', 'select', ['🇹🇼 台湾', '🚀 节点选择', 'DIRECT'])
    grp('📺 ABEMATV', 'select', ['🇯🇵 日本', '🚀 节点选择', 'DIRECT'])
    grp('💚 LINE', 'select', ['🇯🇵 日本', '🚀 节点选择', 'DIRECT'])
    grp('🍎 苹果服务', 'select', ['🚀 节点选择', 'DIRECT'])
    grp('🎮 游戏平台', 'select', ['🚀 节点选择', 'DIRECT'])

    A('  # 以下默认直连。')
    for n in ['Ⓜ️ 微软服务', '💰 支付服务', '🌏 国内媒体', '📀 EMBY']:
        grp(n, 'select', ['DIRECT', '🚀 节点选择'])
    grp('🎯 全球直连', 'select', ['DIRECT', '💴 低倍率', '🚀 节点选择'])
    grp('🇨🇳 国内服务', 'select', ['DIRECT', '🚀 节点选择'])
    grp('🛑 广告拦截', 'select', ['REJECT', 'REJECT-DROP', 'DIRECT'])
    grp('🍃 应用净化', 'select', ['REJECT', 'REJECT-DROP', 'DIRECT'])
    grp('🐟 漏网之鱼', 'select', ['🚀 节点选择', 'DIRECT', '♻️ 自动选择'])
    A('')

    # ---- rule-providers ----
    A('# 规则集。domain 与 ipcidr 是为海量规则优化的加载器，classical 效率最低，')
    A('# 所以按类型拆开而不是整份丢给 classical。')
    A('rule-providers: #!replace')
    for pname, behavior, fname in providers:
        A('  %s:' % pname)
        A('    type: http')
        A('    behavior: %s' % behavior)
        A('    format: text')
        A('    url: %s%s' % (BASE, fname))
        A('    path: ./ruleset/%s' % fname)
        A('    interval: 86400')
    A('')

    # ---- rules ----
    A('# 规则顺序与 Shadowrocket 配置一致，几处刻意安排见 README。')
    A('rules: #!replace')
    A('  # 高频埋点用 REJECT-DROP：静默丢包让 App 等超时才重试，')
    A('  # 日志实测 rmonitor.qq.com 从 1592 次/小时降到 9 次/小时。')
    for d in ['rmonitor.qq.com', 'h.trace.qq.com']:
        A('  - DOMAIN,%s,REJECT-DROP' % d)
    for d in ['jpush.cn', 'jpush.io', 'pangolin-sdk-toutiao1.com', 'pangle.io',
              'iadsdk.apple.com']:
        A('  - DOMAIN-SUFFIX,%s,REJECT-DROP' % d)
    A('  # LinkedIn 中国 2023 年停运，国内 DNS 仍会把 linkedin.com 解析到国内 IP，')
    A('  # 不显式指定就会被后面的 GEOIP,CN 判成国内服务走直连。')
    for d in ['linkedin.com', 'licdn.com', 'linkedin-ei.com', 'linkedin.cn', 'licdn.cn']:
        A('  - DOMAIN-SUFFIX,%s,🚀 节点选择' % d)
    A('  # 境外 AI 服务，上游规则集未覆盖。')
    for d in ['poe.com', 'huggingface.co', 'hf.co', 'cursor.sh', 'cursor.com',
              'midjourney.com']:
        A('  - DOMAIN-SUFFIX,%s,🤖 AI 服务' % d)
    A('  # 游戏本体下载走直连，否则几十 GB 烧机场套餐。')
    for d in ['steampipe.akamaized.net', 'steampipe-kr.akamaized.net',
              'steampipe-partner.akamaized.net', 'steamcdn-a.akamaihd.net',
              'steamusercontent-a.akamaihd.net', 'steamcontent.tnkjmec.com',
              'blzddist1-a.akamaihd.net', 'blzddistkr1-a.akamaihd.net',
              'blzmedia-a.akamaihd.net', 'blznav.akamaized.net',
              'blizzcon-a.akamaihd.net', 'blz-contentstack.com', 'eac-cdn.com']:
        A('  - DOMAIN-SUFFIX,%s,🎯 全球直连' % d)
    A('  # 拼多多与 .cn 顶级域。')
    for d in ['pinduoduo.com', 'pinduoduo.net', 'pddpic.com', 'yangkeduo.com']:
        A('  - DOMAIN-SUFFIX,%s,🇨🇳 国内服务' % d)
    A('  - DOMAIN-SUFFIX,cn,🇨🇳 国内服务')
    A('  # 全部 Apple 流量走代理。')
    for d in ['apple.com', 'apple.news', 'aaplimg.com', 'icloud.com',
              'icloud-content.com', 'cdn-apple.com', 'mzstatic.com',
              'apple-cloudkit.com', 'apple-mapkit.com', 'itunes.com', 'me.com']:
        A('  - DOMAIN-SUFFIX,%s,🍎 苹果服务' % d)
    A('  - IP-CIDR,17.0.0.0/8,🍎 苹果服务,no-resolve')
    rule_lines_sorted = rule_lines
    out.extend([''] + ['  # 规则集'] + rule_lines_sorted)
    A('  - GEOIP,CN,🇨🇳 国内服务,no-resolve')
    A('  - MATCH,🐟 漏网之鱼')
    A('')

    with io.open(OVERRIDE, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(out))

    print('规则集文件 %d 个  domain %d 条 / ipcidr %d 条 / classical %d 条'
          % (len(providers), stats['domain'], stats['ipcidr'], stats['classical']))
    print('合计 %d 条' % sum(stats.values()))
    print('覆写文件: config/stash.stoverride  (%d 行)' % len(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
