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
# 自建 Cloudflare 节点（edgetunnel 一类：Pages/Workers + 优选 IP）。
# 它们只经 ☁️ CF优选 手动启用，不进任何自动测速组：
# - 十几个「节点」是同一后端的不同入口 IP，出口都在 Cloudflare，落地地区
#   取决于命中的机房，不是按地区选出的线路；
# - 优选 IP 延迟通常最低，进了自动测速会把流量悄悄转到 CF 上，
#   而免费额度每天 10 万次请求，每条 WS 连接计一次。
# 必须显式排除，不能依赖 EXCLUDE_INFO 里「官方」对「CF官方优选」的误伤——
# 节点在 edgetunnel 里改名成「CF优选」就会漏进来。
# CF 两侧用逆序环视：不命中 NCF / CFO，也不命中机场的 CTCU（那是 CF 中转，
# 归 DIRECTS 的判据管）。
CF_NODE = '(?<![A-Za-z])CF(?![A-Za-z])|Cloudflare'
CF_GROUP = ('☁️ CF优选', '(?i)(' + CF_NODE + ')')

# 自动测速类分组的 filter：排除信息类伪节点与自建 CF 节点。
AUTO_FILTER = '(?i)^(?!.*(' + EXCLUDE_INFO + '|' + CF_NODE + ')).*$'


# 地区分组的节点名正则。英文缩写一律用逆序环视包裹——裸 US 在忽略大小写下
# 会匹配 Russia / Australia / Brussels / Plus，把俄罗斯澳洲节点混进美国组。
REGIONS = [
    ('🇭🇰 香港', r'(?i)(香港|港岛|深港|沪港|Hong ?Kong|(?<![A-Za-z])(HK|HKG)(?![A-Za-z]))'),
    ('🇹🇼 台湾', r'(?i)(台湾|台灣|台北|臺灣|Taiwan|(?<![A-Za-z])(TW|TPE)(?![A-Za-z]))'),
    ('🇯🇵 日本', r'(?i)(日本|東京|东京|大阪|名古屋|埼玉|Japan|(?<![A-Za-z])(JP|JPN|NRT|KIX)(?![A-Za-z]))'),
    ('🇸🇬 狮城', r'(?i)(新加坡|狮城|獅城|Singapore|(?<![A-Za-z])(SG|SIN)(?![A-Za-z]))'),
    ('🇺🇲 美国', r'(?i)(美国|美國|美西|美东|美東|洛杉矶|圣何塞|西雅图|达拉斯|凤凰城|United ?States|(?<![A-Za-z])(US|USA|LAX|SJC)(?![A-Za-z]))'),
    # 🇰🇷 韩国 已移除：订阅里没有韩国节点，空组在 Shadowrocket 里只剩 DIRECT，
    # default.conf 已注释掉它，这里保持一致。有韩国节点后两边一起加回：
    # ('🇰🇷 韩国', r'(?i)(韩国|韓國|首尔|首爾|Korea|(?<![A-Za-z])(KR|ICN)(?![A-Za-z]))'),
    ('🇬🇧 英国', r'(?i)(英国|英國|伦敦|倫敦|London|(?<![A-Za-z])(UK|GB|LHR)(?![A-Za-z]))'),
]

# 线路属性分组。取自节点名的实际标签，与地区维度正交。
ATTRS = [
    ('🏠 住宅IP', r'(?i)(住宅|家宽|家寬|原生|Residential)'),
    ('🛣️ 专线', r'(?i)(专线|專線|IPLC|IEPL)'),
    ('🎞️ 流媒体节点', r'(?i)(流媒体|流媒體)'),
    ('💴 低倍率', r'(?i)(?<![0-9.])0\.[0-9]+ ?x'),
]

# 直连线路分组：地区 × 无中转。与 Shadowrocket 的同名分组同一判据。
#
# 判据是节点名里单独出现的 CTCU 标签 = Cloudflare 中转（network: ws）。
# 用机场订阅的真实传输方式核对：15 个中转全带裸 CTCU，39 个直连里只有
# 1 个带（被误排除，安全方向）。CTCUCM 是三网直连，用 (?![A-Za-z]) 截断。
# 这是当前机场的命名规律，换机场后用 tools/check-direct.py 重新核对。
#
# mihomo 用 dlclark/regexp2（.NET 兼容），Stash 用系统正则，两者都支持
# (?= / (?! / (?<!，所以直接用单条带环视的 filter，与 Shadowrocket 一致。
# 韩国不开：订阅里没有韩国节点。
RELAY_EXCLUDE = r'(?!.*(?<![A-Za-z])CTCU(?![A-Za-z]))(?!.*(中转|中轉|隧道|转发|轉發))'
DIRECT_REGIONS = ['🇺🇲 美国', '🇯🇵 日本', '🇸🇬 狮城', '🇭🇰 香港', '🇹🇼 台湾', '🇬🇧 英国']


def direct_filter(region_rx):
    """把地区正则改写成「该地区 且 非中转」。"""
    inner = region_rx[4:] if region_rx.startswith('(?i)') else region_rx
    return '(?i)^(?=.*' + inner + ')' + RELAY_EXCLUDE + '.*$'


DIRECTS = [(n + '直连', direct_filter(rx)) for n, rx in REGIONS if n in DIRECT_REGIONS]
DIRECTS.sort(key=lambda d: DIRECT_REGIONS.index(d[0][:-2]))

# 信息类伪节点（「剩余流量：412 GB」「套餐到期：…」）排除。所有按节点名筛选的
# 组都要带：「GB」会被英国组当成地区标签，把一个 ws 中转伪节点收进 🇬🇧 英国直连。
# 与 Shadowrocket 各组用同一段。
PSEUDO = '(?!.*(?:剩余|剩餘|流量|到期|过期|過期|重置|套餐|官网|官網|Expire|Traffic|Reset))'


def guard(f):
    """给筛选正则加上伪节点排除。"""
    if f.startswith('(?i)^'):
        return '(?i)^' + PSEUDO + f[5:]
    inner = f[4:] if f.startswith('(?i)') else f
    return '(?i)^' + PSEUDO + '(?=.*' + inner + ').*$'


# ---------------------------------------------------------------------------
# 从 config/default.conf 推导规则与分组候选。
#
# 以前 Stash / Clash 的内联规则、规则顺序、业务组候选都是在生成器里手写的第二份
# 拷贝，于是反复漂移：2026-09-24 审查实测 Stash / Clash 把 DOMAIN-SUFFIX,cn 与
# apple.com 排在广告规则集之前，Shadowrocket 会拦的 3402 个广告域名在这两边
# 全部放行；Steam CDN、MapKit、Claude 等内联规则缺失；Clash 的微软、EMBY 默认
# 出口也与另外两份不同。生成器里还写着「规则顺序与 Shadowrocket 配置一致」。
#
# 现在 default.conf 是唯一来源：[Rule] 逐行翻译，select 组的候选照抄。
# ---------------------------------------------------------------------------
SR_CONFIG = os.path.join(ROOT, 'config', 'default.conf')
SR_RULE_TYPES = {'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR', 'IP-CIDR6',
                 'GEOIP', 'USER-AGENT'}


def _sr_section(name):
    out, inside = [], False
    for line in io.open(SR_CONFIG, encoding='utf-8').read().splitlines():
        s = line.strip()
        if s.startswith('[') and s.endswith(']'):
            inside = (s == name)
            continue
        if inside:
            out.append(s)
    return out


def sr_rules():
    """把 [Rule] 解析成有序条目：('comment', 文本) / ('rule', 行) /
    ('set', slug, 策略) / ('final', 策略)。遇到不认识的类型直接报错——
    静默跳过会让两边悄悄不一致，那正是这次要消灭的问题。"""
    out = []
    for s in _sr_section('[Rule]'):
        if not s:
            continue
        if s.startswith('#'):
            out.append(('comment', s.lstrip('#').strip()))
            continue
        parts = [x.strip() for x in s.split(',')]
        t = parts[0]
        if t == 'RULE-SET':
            slug = parts[1].rsplit('/', 1)[-1].replace('.list', '')
            out.append(('set', slug, parts[2]))
        elif t == 'FINAL':
            out.append(('final', parts[1]))
        elif t in SR_RULE_TYPES:
            out.append(('rule', ','.join(parts)))
        else:
            raise SystemExit('default.conf [Rule] 出现生成器不认识的类型 %s：%s' % (t, s))
    return out


def sr_select_groups():
    """[Proxy Group] 里的 select 组：名字 -> (候选列表, policy-select-name)。
    带 policy-regex-filter 的 select 组（🔧 手动选择）没有显式候选，不在此列。"""
    out = {}
    for s in _sr_section('[Proxy Group]'):
        if not s or s.startswith('#') or '=' not in s:
            continue
        name, body = s.split('=', 1)
        parts = [x.strip() for x in body.split(',')]
        if parts[0] != 'select' or any(x.startswith('policy-regex-filter') for x in parts):
            continue
        cands = [x for x in parts[1:] if x and '=' not in x]
        psn = next((x.split('=', 1)[1].strip() for x in parts
                    if x.startswith('policy-select-name')), None)
        out[name.strip()] = (cands, psn)
    return out


def derived_candidates(name, known, extra=None):
    """按 default.conf 给出某个 select 组的候选：policy-select-name 挪到首位
    （Stash / Clash 没有这个键，select 组默认选第一项），过滤掉目标配置里
    不存在的名字。extra 是目标独有的附加候选，插在 DIRECT 之前。"""
    cands, psn = sr_select_groups()[name]
    if psn:
        cands = [psn] + [c for c in cands if c != psn]
    cands = [c for c in cands if c in known]
    if extra:
        tail = [c for c in cands if c == 'DIRECT']
        cands = [c for c in cands if c != 'DIRECT'] + [e for e in extra if e not in cands] + tail
    return cands


def emit_rules(A, kinds, indent='  '):
    """把 [Rule] 翻译成 Stash / Clash 的 rules。kinds: slug -> 已生成的 behavior 列表。
    RULE-SET 按 domain / ipcidr / classical 展开，ipcidr 一律带 no-resolve。"""
    n = 0
    for item in sr_rules():
        if item[0] == 'comment':
            A('%s# %s' % (indent, item[1]) if item[1] else indent + '#')
        elif item[0] == 'rule':
            A('%s- %s' % (indent, item[1])); n += 1
        elif item[0] == 'set':
            slug, pol = item[1], item[2]
            if slug not in kinds:
                raise SystemExit('default.conf 引用了 rule/%s.list，但没有为它生成规则集' % slug)
            for kind in kinds[slug]:
                A('%s- RULE-SET,%s-%s,%s%s' % (indent, slug, kind, pol,
                                              ',no-resolve' if kind == 'ipcidr' else ''))
                n += 1
        elif item[0] == 'final':
            A('%s- MATCH,%s' % (indent, item[1])); n += 1
    return n


# 协议分组：Stash 做不到，已移除。
#
# 曾用 exclude-type 实现，但 Stash 官方文档的 proxy-groups 选项里没有这一项
# （只有 filter / include-all / interval / lazy / strategy 等）。当初是查
# Mihomo 文档确认的，而 Stash 兼容 Clash Premium 但引擎自研，未实现它。
#
# 更麻烦的是：exclude-type 不被支持时不报错、只静默忽略，于是「VLESS」组
# 实际等于「全部节点」，Hy2 混在里面。是在设备上用出来才发现的。
#
# filter 只能匹配节点名，而机场的节点名里没有协议信息（13 个真实节点名实测
# 0 个含协议关键词），所以这个维度在 Stash 上与 Shadowrocket 同样受限。
#
# 若机场提供按协议区分的订阅地址，可用 proxy-providers 分别引入再 use: 指向，
# 那是另一条可行路径。

# 用 https：机场劫持明文 204 测速地址很常见。开了 unified-delay 后 mihomo
# 会发两次 HEAD，被劫持时第二次超时，好节点反而被判失败踢出候选。
TEST_URL = 'https://www.gstatic.com/generate_204'

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
    kinds = {}              # slug -> 实际生成了的 behavior，供 emit_rules 展开 RULE-SET
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
            providers.append(('%s-%s' % (slug, kind), kind, fname))
            kinds.setdefault(slug, []).append(kind)

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
    A('desc: 规则与分组候选由 default.conf 推导，与 Shadowrocket 一致。含地区、直连线路、线路属性分组。')
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
    A('  # 收录原则：只放【走直连 + 需要真实 IP】的域名。')
    A('  # 走代理的域名不放——对它们 fake-ip 恰恰是优点（免本地解析、免污染）。')
    A('  # 需要真实 IP 的三类：局域网/内网发现、NTP 校时、')
    A('  # 以及靠出口 IP 判定版权区域的国内音乐服务。')
    A('  # 常见配置里的 battle.net / srv.nintendo.net / pvp.net 一类不收：')
    A('  # 本配置把游戏平台整体送去代理，对代理域名 fake-ip 才是正解，')
    A('  # 放进来只会白白多一次本地解析并泄漏域名。check-config.py 会拦。')
    A('  fake-ip-filter:')
    A('  # 保留域与局域网')
    A('    - "*.lan"')
    A('    - "*.local"')
    A('    - "*.localdomain"')
    A('    - "*.home.arpa"')
    A('    - "*.invalid"')
    A('    - "*.localhost"')
    A('    - "*.test"')
    A('    - "*.example"')
    A('  # 路由器 / 智能家居管理页，域名指向局域网网关地址')
    A('    - "*.router.asus.com"')
    A('    - "*.linksys.com"')
    A('    - "*.linksyssmartwifi.com"')
    A('    - "heartbeat.belkin.com"')
    A('  # 系统联网检测：拿到假 IP 会被判成「网络受限」，反复弹强制门户')
    A('    - "+.msftconnecttest.com"')
    A('    - "+.msftncsi.com"')
    A('    - "+.ipv6.microsoft.com"')
    A('    - "localhost.ptlogin2.qq.com"')
    A('    - "localhost.sec.qq.com"')
    A('  # NTP 校时走 UDP 123。假 IP 对不上时间，会连锁导致 TLS 证书校验失败。')
    A('  # 只列具体的直连校时主机。之前用 time.*.com / ntp*.*.com 这类泛通配，')
    A('  # 会命中走代理的域名（例如 time.apple.com 曾随 Apple 走代理），违反上面的')
    A('  # 收录原则，而 check-config 的 check_fakeip 跳过带内部通配的条目，从没查到。')
    A('    - "time.apple.com"')
    A('    - "time-ios.apple.com"')
    A('    - "time-macos.apple.com"')
    A('    - "time.asia.apple.com"')
    A('    - "time.euro.apple.com"')
    A('    - "time.windows.com"')
    A('    - "ntp.aliyun.com"')
    A('    - "ntp1.aliyun.com"')
    A('    - "ntp2.aliyun.com"')
    A('    - "ntp3.aliyun.com"')
    A('    - "ntp4.aliyun.com"')
    A('    - "ntp5.aliyun.com"')
    A('    - "ntp6.aliyun.com"')
    A('    - "ntp7.aliyun.com"')
    A('    - "ntp.tencent.com"')
    A('    - "ntp1.tencent.com"')
    A('    - "ntp2.tencent.com"')
    A('    - "ntp3.tencent.com"')
    A('    - "ntp4.tencent.com"')
    A('    - "ntp5.tencent.com"')
    A('    - "time1.cloud.tencent.com"')
    A('    - "time2.cloud.tencent.com"')
    A('    - "time3.cloud.tencent.com"')
    A('    - "time4.cloud.tencent.com"')
    A('    - "time5.cloud.tencent.com"')
    A('    - "*.time.edu.cn"')
    A('    - "*.ntp.org.cn"')
    A('    - "+.pool.ntp.org"')
    A('  # STUN 泛通配（stun.* 等）已去掉：stun.l.google.com 这类走代理，与收录原则')
    A('  # 冲突；而 P2P 打洞在 Shadowrocket 侧本就被 stun-response-ip 伪造响应挡掉了。')
    A('  # 国内音乐：版权区域按出口 IP 判定，假 IP 会被判成海外 -> 大量歌曲变灰')
    A('    - "music.163.com"')
    A('    - "*.music.163.com"')
    A('    - "*.126.net"')
    A('    - "y.qq.com"')
    A('    - "*.y.qq.com"')
    A('    - "aqqmusic.tc.qq.com"')
    A('    - "amobile.music.tc.qq.com"')
    A('    - "streamoc.music.tc.qq.com"')
    A('    - "mobileoc.music.tc.qq.com"')
    A('    - "isure.stream.qqmusic.qq.com"')
    A('    - "dl.stream.qqmusic.qq.com"')
    A('    - "songsearch.kugou.com"')
    A('    - "trackercdn.kugou.com"')
    A('    - "*.kuwo.cn"')
    A('    - "music.migu.cn"')
    A('    - "*.music.migu.cn"')
    A('    - "music.taihe.com"')
    A('    - "musicapi.taihe.com"')
    A('  # B 站 PCDN 要真实 IP 做点对点回源')
    A('    - "*.mcdn.bilivideo.cn"')
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

    # ---- http: mitm + url-rewrite ----
    A('# HTTP 引擎。本覆写自身只做 google.cn 系列重写，iRingo 等覆写各自带 mitm 与 script，')
    A('# 叠加时会合并，不需要在此手抄它们的域名。')
    A('http:')
    A('  # 重写要生效必须同时满足：域名在 mitm 列表里 + 证书已安装并信任。')
    A('  # 只列本覆写自己需要解密的域名，列表越小性能与风险越低。')
    A('  mitm:')
    A('    - "g.cn"')
    A('    - "*.g.cn"')
    A('    - "www.google.cn"')
    A('    - "*.google.cn"')
    A('  # google.cn / g.cn 是 Google 中国的旧域名，2010 年退出后只剩跳转落地页。')
    A('  # 重写成 www.google.com，让旧书签、二维码、App 内链接直接落到真正的 Google。')
    A('  url-rewrite:')
    A(r'    - ^https?://(www\.)?g\.cn https://www.google.com 302')
    A(r'    - ^https?://(www\.)?google\.cn https://www.google.com 302')
    A('  # 地图子域单独指到 Google 地图，落到 google.com 首页等于丢掉用户意图。')
    A(r'    - ^https?://(ditu|maps)\.google\.cn https://maps.google.com 302')
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

    # 按节点名筛选的组。filter 一律过 guard()，排除信息类伪节点。
    # include-all 不可省：filter 只对 use 引入的 provider 或 include-all 之后的
    # 全体节点生效，两者都没有时组内无节点可筛，会是空组。
    A('  # 自动测速类。filter 排除机场的信息类伪节点与自建 CF 节点。')
    grp('♻️ 自动选择', 'url-test', None, include_all='true', filter=q(AUTO_FILTER),
        url=q(TEST_URL), interval=300, tolerance=100, lazy='true')
    grp('🔯 故障转移', 'fallback', None, include_all='true', filter=q(AUTO_FILTER),
        url=q(TEST_URL), interval=300, lazy='true')
    grp('🔮 负载均衡', 'load-balance', None, include_all='true', filter=q(AUTO_FILTER),
        url=q(TEST_URL), interval=300, strategy='consistent-hashing')
    A('  # 手动挑单个节点用。上面几组都是自动的，没有这一组就只能选组不能选节点。')
    grp('🔧 手动选择', 'select', None, include_all='true', filter=q(AUTO_FILTER))

    filtered = ([(n, f) for n, f in ATTRS] + [(n, f) for n, f in REGIONS]
                + [(n, f) for n, f in DIRECTS] + [CF_GROUP])
    A('  # 线路属性、地区、直连线路（地区 × 无中转，判据见文件头 DIRECTS）、自建 CF。')
    for name, f in filtered:
        grp(name, 'url-test', None, include_all='true', filter=q(guard(f)),
            url=q(TEST_URL), interval=600, tolerance=200, lazy='true')

    # select 组：候选一律照抄 default.conf，policy-select-name 挪到首位。
    known = ({'♻️ 自动选择', '🔯 故障转移', '🔮 负载均衡', '🔧 手动选择',
              'DIRECT', 'REJECT', 'REJECT-DROP'}
             | {n for n, _ in filtered} | set(sr_select_groups()))
    A('  # 以下 select 组的候选由 tools/build-stash.py 从 config/default.conf 照抄，')
    A('  # Shadowrocket 的 policy-select-name 在这里体现为「排第一」。')
    for name in sr_select_groups():
        grp(name, 'select', derived_candidates(name, known))
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
    A('# 规则由 tools/build-stash.py 从 config/default.conf 的 [Rule] 逐行翻译，')
    A('# 顺序与内联规则与 Shadowrocket 完全一致；注释也一并带过来。')
    A('# RULE-SET 按 domain / ipcidr / classical 展开成本仓库的规则集，FINAL 写作 MATCH。')
    A('rules: #!replace')
    nrules = emit_rules(A, kinds)
    A('')

    with io.open(OVERRIDE, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(out))

    print('规则集文件 %d 个  domain %d 条 / ipcidr %d 条 / classical %d 条'
          % (len(providers), stats['domain'], stats['ipcidr'], stats['classical']))
    print('合计 %d 条' % sum(stats.values()))
    print('覆写文件: config/stash.stoverride  (%d 行，规则 %d 条)' % (len(out), nrules))
    return 0


if __name__ == '__main__':
    sys.exit(main())
