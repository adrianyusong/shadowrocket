#!/usr/bin/env python3
"""配置与规则集的静态校验。任何一项失败退出码非 0。

这些检查全部来自真实踩过的坑：
  * 策略引用拼写不一致  —— 台湾组曾写成 U+1F1FC U+1F1F8 而定义是 U+1F1F9 U+1F1FC，规则静默失效
  * 孤儿策略组          —— 负载均衡组定义了却无人引用
  * 混合大小写          —— Shadowrocket 会把 [Rule] 里的 ASCII 策略名转大写
  * 顺序约束            —— SteamCN 必须先于 Steam，否则几十 GB 下载走代理
  * 危险关键词          —— DOMAIN-KEYWORD,jav 误伤 javascript.info
  * 误伤真实服务        —— licdn 是 alicdn 的子串
  * workflow YAML 语法  —— heredoc 内容顶格会冲出 run: | 的块作用域
  * skip-proxy 漏网    —— 该层在规则之前生效，命中即绕过隧道，规则拦不住
  * 丢失 no-resolve    —— 合并时丢掉修饰符，IP 规则会为每个域名请求触发本地 DNS
  * 凭据入库            —— 仓库是公开的，订阅地址 / 代理链接 / MITM 私钥一旦提交就已泄漏
  * 词表与参数各抄一份  —— 伪节点排除词、测速参数在三份配置里各写各的，互相漏词、数值漂移
  * ipv6: false         —— 在 Clash for Apple 上是「隧道不接管 IPv6」，IPv6 连接整个绕过代理

用法：
    python tools/check-config.py
"""
import collections
import fnmatch
import glob
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, 'config', 'default.conf')
RULEDIR = os.path.join(ROOT, 'rule')

# REJECT-VIDEO 与 TAILSCALE 见官方懒人配置 2026-08-07 版
BUILTIN = {'DIRECT', 'REJECT', 'REJECT-DROP', 'REJECT-NO-DROP', 'REJECT-TINYGIF',
           'REJECT-IMG', 'REJECT-200', 'REJECT-DICT', 'REJECT-ARRAY',
           'REJECT-VIDEO', 'TAILSCALE', 'PROXY'}

# (先, 后) —— 含前者的规则行必须出现在含后者的规则行之前
ORDER = [
    ('rmonitor.qq.com', 'reject-ads'),
    ('jpush.cn', 'reject-ads'),
    ('linkedin.com', 'china.list'),
    ('jpush.cn', 'DOMAIN-SUFFIX,cn'),
    ('linkedin.com', 'DOMAIN-SUFFIX,cn'),
    ('linkedin.com', 'GEOIP'),
    ('pinduoduo.com', 'GEOIP'),
    ('ai.list', 'proxy.list'),
    ('direct-global.list', 'game.list'),
    ('china.list', 'GEOIP'),
    ('DOMAIN-SUFFIX,apple.com', 'GEOIP'),
    ('IP-CIDR,17.0.0.0/8', 'GEOIP'),
    # 去重改按 [Rule] 顺序后，这几条先后决定重复规则归哪个策略（sync-rules.py 的
    # order_by_rule）。颠倒后下次同步就会换组，例如 bing.com 只在 bing.list 里，
    # 排到 microsoft.list 之后就归 Ⓜ️ 微软服务 走直连。
    ('ai.list', 'google.list'),
    ('ai.list', 'microsoft.list'),
    ('ai.list', 'twitter.list'),
    ('bing.list', 'microsoft.list'),
    ('apple-media.list', 'apple.list'),
    ('biliintl.com', 'media-cn.list'),
    ('media-global.list', 'media-cn.list'),
    # 内联规则必须压过后面的规则集
    ('android.apis.google.com', 'ai.list'),
    ('adservice.google.', 'google.list'),
    # APNs 单独成组，必须压过 apple.list 的 apple.com 全域后缀
    ('push.apple.com', 'apple.list'),
]

# 正常服务不该被任何拦截类策略命中
MUST_NOT_BLOCK = [
    'www.linkedin.com', 'media.licdn.cn', 'api.pinduoduo.com', 'www.qq.com',
    'api.github.com', 'api.openai.com', 'www.google.com', 'weatherkit.apple.com',
    'mobilegw.alipay.com', 'acs.m.taobao.com', 'www.youtube.com', 'gw.alicdn.com',
    'dispatcher.is.autonavi.com', 'steampipe.akamaized.net', 'javascript.info',
    'javadoc.io',
    # 2026-09 审查实测被 anti-AD 关键词或误分类拦掉的正常服务
    'openxlab.org.cn', 'm.suning.com', 'example.wixsite.com', 'crl.microsoft.com',
    'dns.weixin.qq.com.cn', 'geeksquadservices.org',
]

# 必须落到指定策略的主机（按 [Rule] 顺序逐条模拟首条命中）。
# 每条都对应一次实测踩过的坑，改规则或同步上游后由它兜住。
MUST_ROUTE = {
    # anti-AD 短关键词剔除后，各国家后缀的 adservice 靠内联 KEYWORD 补回
    'adservice.google.com.sg': '🛑 广告拦截',
    'adservice.google.co.jp': '🛑 广告拦截',
    'servedbyopenx.com': '🛑 广告拦截',
    'adformdsp.net': '🛑 广告拦截',
    # 国服游戏：从游戏集剔除 .cn 后交给 DOMAIN-SUFFIX,cn 直连
    'client01.pdl.wow.battlenet.com.cn': '🇨🇳 国内服务',
    'static.blzstatic.cn': '🇨🇳 国内服务',
    'www.nintendoswitch.com.cn': '🇨🇳 国内服务',
    'lol.leagueoflegends.cn': '🇨🇳 国内服务',
    # FCM 的 Android 端点，ai.list 的 apis.google.com 后缀会抢走它
    'android.apis.google.com': '📢 谷歌服务',
    'mtalk.google.com': '📢 谷歌服务',
    # 去重顺序相关
    'www.bing.com': '🤖 AI 服务',
    'ocsp.digicert.com': '🎯 全球直连',
    # README「规则顺序」表各行的关键主机。ORDER 只查位置，这里查去向——
    # 改了某行的策略（例如埋点从 REJECT-DROP 改成 REJECT），ORDER 查不出来。
    'rmonitor.qq.com': 'REJECT-DROP',          # 埋点用 DROP 抑制重试
    'api.jpush.cn': 'REJECT-DROP',             # DROP 压过 reject-ads 里的 jpush.cn
    # LinkedIn：.cn 的两个主机守「压过 .cn」；www.linkedin.com 在 proxy.list 里也有，
    # 只核对去向。「压过 GEOIP」不在模拟范围内，只靠 ORDER 的位置约束。
    'www.linkedin.com': '🚀 节点选择',
    'www.linkedin.cn': '🚀 节点选择',
    'media.licdn.cn': '🚀 节点选择',
    'cn.bing.com': '🔍 BING',                  # Bing 压过 Microsoft
    'www.ifeng.com': '🇨🇳 国内服务',            # 只靠 china.list 接住的国内域名
    'ad.12306.cn': '🛑 广告拦截',               # 广告规则压过 .cn
    'searchads.apple.com': '🛑 广告拦截',       # 广告规则压过 apple.list
    'appleid.apple.com': '🍎 苹果服务',         # AppleID 同走苹果服务
    'steampipe.akamaized.net': '🎯 全球直连',   # 游戏下载 CDN 压过 game.list
    'blzddist1-a.akamaihd.net': '🎯 全球直连',
    'www.speedtest.net': '🎯 全球直连',         # 测本地真实带宽
    'api.biliintl.com': '🌍 国外媒体',          # B 站国际版压过 media-cn
    'gemini.google.com': '🤖 AI 服务',          # AI 压过 google.list
    # APNs 单独成组、默认直连：随 🍎 苹果服务 走代理时，自动测速组一换节点长连接就断
    '1-courier.push.apple.com': '🔔 苹果推送',
    'init-p01st.push.apple.com': '🔔 苹果推送',
}

# fake-ip-filter 里的通配条目无法直接映射到域名，拿这组常见真实主机去撞。
# 撞中且走代理即判失败。time.apple.com 就是 time.*.com 当年漏查的那个。
WILDCARD_PROBES = [
    'time.apple.com', 'time-ios.apple.com', 'time.google.com', 'time.windows.com',
    'time.cloudflare.com', 'time.facebook.com', 'time.nist.gov', 'ntp.aliyun.com',
    'ntp.tencent.com', 'time1.cloud.tencent.com', 'stun.l.google.com',
    'stun.cloudflare.com', 'stun.qq.com', 'global.stun.twilio.com',
    'router.asus.com', 'www.linksyssmartwifi.com', 'music.163.com', 'y.qq.com',
]

# IP 类规则可带尾部修饰符，取策略名时必须先剥掉，
# 否则会把 no-resolve 当成策略名并误报未定义。
MODIFIERS = {'no-resolve', 'extended-matching', 'pre-matching', 'force-remote-dns'}

FAILS = []
WARNS = []


def policy_of(line):
    """取规则行的策略名，忽略尾部修饰符。"""
    parts = [x.strip() for x in line.split(',')]
    while len(parts) > 2 and parts[-1] in MODIFIERS:
        parts.pop()
    return parts[-1]


def fail(msg):
    FAILS.append(msg)


def warn(msg):
    WARNS.append(msg)


def _tool(name):
    """载入 tools/ 下的另一个脚本。文件名带连字符，不能直接 import。"""
    import importlib.util
    cache = _tool.__dict__.setdefault('cache', {})
    if name not in cache:
        spec = importlib.util.spec_from_file_location(
            name[:-3].replace('-', '_'), os.path.join(ROOT, 'tools', name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cache[name] = mod
    return cache[name]


def sections(cfg):
    """产出 (section, lineno, stripped_line)，跳过空行与注释。"""
    section = None
    for i, line in enumerate(cfg.split('\n'), 1):
        s = line.strip()
        if s.startswith('[') and s.endswith(']'):
            section = s
            continue
        if not s or s.startswith('#'):
            continue
        yield section, i, s


def check_policy_refs(cfg):
    defined = [s.split('=')[0].strip()
               for sec, i, s in sections(cfg) if sec == '[Proxy Group]']
    used = set()
    refs = []
    for sec, i, s in sections(cfg):
        if sec == '[Proxy Group]':
            own = s.split('=')[0].strip()
            body = '='.join(s.split('=')[1:])
            for x in [y.strip() for y in body.split(',')][1:]:
                # 跳过 key=value 参数（policy-select-name / interval / url 等），
                # 它们不是策略引用
                if '=' in x or not x:
                    continue
                refs.append((i, x))
                if x != own:
                    used.add(x)
        elif sec == '[Rule]':
            target = policy_of(s)
            refs.append((i, target))
            used.add(target)

    for i, x in refs:
        if x not in defined and x not in BUILTIN:
            fail('L%d 引用了未定义的策略: %s' % (i, x))
    for g in defined:
        if g not in used:
            fail('孤儿策略组，定义了但无任何引用: %s' % g)
    for g, n in collections.Counter(defined).items():
        if n > 1:
            fail('策略组重复定义: %s' % g)
    return len(defined), len(refs)


def check_select_name(cfg):
    """policy-select-name 指定的默认值必须出现在该组的候选里，否则不生效。"""
    for sec, i, s in sections(cfg):
        if sec != '[Proxy Group]' or 'policy-select-name' not in s:
            continue
        name = s.split('=')[0].strip()
        body = '='.join(s.split('=')[1:])
        parts = [x.strip() for x in body.split(',')]
        want = None
        cands = []
        for x in parts[1:]:
            if x.startswith('policy-select-name='):
                want = x.split('=', 1)[1].strip()
            elif '=' not in x and x:
                cands.append(x)
        if want and want not in cands:
            fail('L%d 分组 %s 的 policy-select-name=%s 不在候选列表中，不会生效'
                 % (i, name, want))


def check_case(cfg):
    for sec, i, s in sections(cfg):
        if sec != '[Proxy Group]':
            continue
        name = s.split('=')[0].strip()
        if re.search(r'[a-z]', name) and re.search(r'[A-Z]', name):
            fail('L%d 策略组名混合大小写，Shadowrocket 会在 [Rule] 中转成大写: %s'
                 % (i, name))


def rule_lines(cfg):
    """取出 [Rule] 段的行。

    必须按整行相等来定位段落，不能用 cfg.split('[Rule]')——注释里出现
    "[Rule]" 字样就会把切分点挪到前面，导致整段约束静默失效。
    这正是本函数曾经踩过的坑。
    """
    out, inside = [], False
    for line in cfg.splitlines():
        s = line.strip()
        if s.startswith('[') and s.endswith(']'):
            inside = (s == '[Rule]')
            continue
        if inside:
            out.append(line)
    return out


def check_order(cfg):
    body = rule_lines(cfg)
    if not body:
        fail('找不到 [Rule] 段，顺序约束无法检查')
        return
    pos = {}
    for i, line in enumerate(body):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        for pair in ORDER:
            for token in pair:
                if token in s and token not in pos:
                    pos[token] = i
    for a, b in ORDER:
        # 标记缺失一律判失败：约束静默失效比约束被破坏更危险，
        # 因为后者至少会在日志里表现出来。
        missing = [t for t in (a, b) if t not in pos]
        if missing:
            fail('顺序约束的标记未出现在 [Rule] 段，约束形同虚设: %s < %s（缺 %s）'
                 % (a, b, '、'.join(missing)))
            continue
        if pos[a] >= pos[b]:
            fail('顺序约束被破坏: %s (第%d行) 必须早于 %s (第%d行)'
                 % (a, pos[a], b, pos[b]))


def load_rules():
    out = []
    for path in sorted(glob.glob(os.path.join(RULEDIR, '*.list'))):
        name = os.path.basename(path).replace('.list', '')
        for line in io.open(path, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 2:
                continue
            out.append((parts[0], parts[1], name))
    return out


def check_keywords(rules):
    for rtype, value, name in rules:
        if rtype != 'DOMAIN-KEYWORD':
            continue
        if len(value) <= 3:
            fail('关键词过短，误伤风险高: DOMAIN-KEYWORD,%s (%s.list)' % (value, name))
        elif len(value) <= 4:
            warn('关键词较短，建议核查: DOMAIN-KEYWORD,%s (%s.list)' % (value, name))


def matches(rtype, value, host):
    if rtype == 'DOMAIN':
        return host == value
    if rtype == 'DOMAIN-SUFFIX':
        return host == value or host.endswith('.' + value)
    if rtype == 'DOMAIN-KEYWORD':
        return value in host
    if rtype == 'DOMAIN-WILDCARD':
        # ai.list 的 *mask*.icloud.com / *siri*.apple.com 用的就是它。不认它时，
        # 模拟结果会把 Private Relay 判成 🍎 苹果服务，而设备上它先命中 🤖 AI 服务。
        return fnmatch.fnmatchcase(host, value.lower())
    return False


BLOCK_POLICIES = ('REJECT', '🛑 广告拦截', '🍃 应用净化')


def check_no_block(rules, cfg):
    blockers = {'reject-ads', 'reject-privacy'}
    idx = [(t, v) for t, v, n in rules if n in blockers]
    for sec, i, s in sections(cfg):
        if sec == '[Rule]' and s.startswith('DOMAIN') and not s.startswith('RULE-SET'):
            p = [x.strip() for x in s.split(',')]
            if len(p) >= 3 and p[2].startswith(BLOCK_POLICIES):
                idx.append((p[0], p[1]))
    for host in MUST_NOT_BLOCK:
        for rtype, value in idx:
            if matches(rtype, value, host):
                fail('正常服务被拦截规则命中: %s  <-  %s,%s' % (host, rtype, value))
                break


def check_rulesets_exist(cfg):
    for m in re.finditer(r'RULE-SET,(\S+?)\s*,', cfg):
        url = m.group(1)
        if 'adrianyusong' not in url:
            warn('仍引用外部规则集，收编后应指向本仓库: %s' % url)
            continue
        fname = url.rsplit('/', 1)[-1]
        if not os.path.exists(os.path.join(RULEDIR, fname)):
            fail('配置引用了不存在的规则文件: rule/%s' % fname)


# skip-proxy 命中即绕过隧道，[Rule] 里写什么都无效。
# 目标是「所有 Apple 走代理」，故这里不允许出现 Apple 域名，
# 唯一例外是 WiFi 门户检测——走代理会连不上酒店与机场热点。
# 它照样要过下面的策略核对：Stash / Clash 没有 skip-proxy，那两端全靠 [Rule]
# 把它判直连；以前这里直接放行，于是 Stash / Clash 一直把它随 🍎 苹果服务 送去代理。
SKIP_PROXY_ALLOWED_APPLE = {'captive.apple.com'}

# skip-proxy 命中即绕过规则层。除上面的门户检测外，只允许规则本来就判直连的域名：
# 判拦截的放进来等于让拦截失效，判代理的放进来等于绕过代理，解析也脱离本配置的
# DNS 设置。*.crashlytics.com（Surge 模板遗留，规则判 🛑 广告拦截）就是因为这里
# 以前只查 Apple 域名才一直没被发现。
SKIP_PROXY_OK_POLICIES = {'DIRECT', '🎯 全球直连', '🇨🇳 国内服务', '🌏 国内媒体'}


def check_classical_types():
    """stash/*-classical.txt 不得含 mihomo / Stash 吃不下的規則類型。

    URL-REGEX 是 QuantumultX / Surge 語法，mihomo 與 Stash 的規則引擎都不支援，
    寫進 classical provider 只會載入失敗或被靜默跳過。build-stash.py 的
    convert_url_regex 已在拆分時轉換或丟棄，這裡是第二道防線：防有人手改產物，
    或將來新增上游又把不支援的類型帶進來。類型清單以 build-stash.py 的
    MIHOMO_CLASSICAL_TYPES 為準，不在這裡維護第二份拷貝。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'bs', os.path.join(ROOT, 'tools', 'build-stash.py'))
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    allowed = bs.MIHOMO_CLASSICAL_TYPES
    for path in sorted(glob.glob(os.path.join(STASH_DIR, '*-classical.txt'))):
        for line in io.open(path, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            rtype = line.split(',', 1)[0].strip()
            if rtype not in allowed:
                fail('classical 規則集含 mihomo / Stash 不支援的規則類型，會載入'
                     '失敗或被靜默跳過: %s: %s' % (os.path.basename(path), line))


def check_skip_proxy(cfg):
    for sec, i, s in sections(cfg):
        if not s.startswith('skip-proxy'):
            continue
        items = [x.strip() for x in s.split('=', 1)[1].split(',')]
        for item in items:
            low = item.lower().lstrip('*.')
            if ('apple' in low or 'icloud' in low) and item not in SKIP_PROXY_ALLOWED_APPLE:
                fail('skip-proxy 含 Apple 域名，会绕过隧道使代理规则失效: %s' % item)
                continue
            if re.match(r'^[0-9a-f:.]+(/[0-9]+)?$', low) or low in ('localhost', 'local'):
                continue
            policy = _first_policy(cfg, low)
            if policy not in SKIP_PROXY_OK_POLICIES:
                fail('skip-proxy 的 %s 绕过规则层，而规则把它判给 %s：放在这里等于让该策略'
                     '失效，解析也脱离本配置的 DNS 设置' % (item, policy or 'FINAL（走代理）'))


# 发往这些国内公共 DNS 的明文查询按目的 IP 命中 GEOIP,CN 走直连，App 查的任何域名
# （包括境外的）都以明文交给国内 DNS——这是绕得过 no-resolve 的唯一泄漏路径。
# 境外公共 DNS 原本就随 FINAL 走代理，不强制。
HIJACK_REQUIRED = ['114.114.114.114', '114.114.115.115', '223.5.5.5', '223.6.6.6',
                   '119.29.29.29', '182.254.116.116', '1.12.12.12', '120.53.53.53',
                   '180.76.76.76', '1.2.4.8', '210.2.4.8', '101.226.4.6']


def check_hijack_dns(cfg):
    for sec, i, s in sections(cfg):
        if sec == '[General]' and s.startswith('hijack-dns'):
            have = {x.strip() for x in s.split('=', 1)[1].split(',')}
            miss = [ip for ip in HIJACK_REQUIRED if ip + ':53' not in have]
            if miss:
                fail('hijack-dns 缺少国内公共 DNS: %s。App 硬编码这些地址时，查询按 '
                     'GEOIP,CN 直连、以明文送达国内 DNS，绕过 no-resolve 造成泄漏'
                     % '、'.join(miss))
            return
    fail('[General] 缺少 hijack-dns：App 硬编码的国内 DNS 查询会直连泄漏')


def check_no_resolve():
    """IP 类规则必须保留 no-resolve。

    上游 ChinaMax 的一万两千余条 IP-CIDR 都带该修饰符。丢掉后每条规则都会
    为域名请求触发一次本地 DNS 解析——既把域名泄漏给国内 DNS，又让被污染的
    解析结果把境外域名判成国内。sync-rules.py 曾因只取 (类型, 值) 而全部丢失。
    """
    # 以前只查 china.list、容忍 10% 缺失，于是 apple.list 的 13 条全缺也照样通过——
    # 而 apple.list 在 [Rule] 第 463 行，恰好是第一个触发解析的规则集，把之后所有
    # 走代理的域名都先交给了国内 DoH。缺一条就够泄漏，所以查全部文件、零容忍。
    ip_types = ('IP-CIDR,', 'IP-CIDR6,', 'IP6-CIDR,', 'IP-ASN,')
    for path in sorted(glob.glob(os.path.join(RULEDIR, '*.list'))):
        miss = [line.strip() for line in io.open(path, encoding='utf-8')
                if line.startswith(ip_types) and 'no-resolve' not in line]
        if miss:
            fail('%s 有 %d 条 IP 规则缺 no-resolve（例 %s）：走到这里的域名会先经'
                 '本地 DNS 解析，代理域名因此泄漏' % (os.path.basename(path), len(miss), miss[0]))
    # 配置里的内联 IP 规则同理。
    for sec, i, s in sections(io.open(CONFIG, encoding='utf-8').read()):
        if sec == '[Rule]' and s.startswith(ip_types + ('GEOIP,',)) and 'no-resolve' not in s:
            fail('L%d 内联 IP 规则缺 no-resolve: %s' % (i, s))


def _expand(expr):
    """把 host 表达式里的正则分组展开成所有具体域名。

    news(-todayconfig)?-edge.apple.com 展开为
    news-edge.apple.com 与 news-todayconfig-edge.apple.com。
    不展开的话，含可选分组的 pattern 会在后续处理中被 ? 截断而静默丢失——
    检查看似通过，实则从未核对过那些域名。
    """
    m = re.search(r'\(([^()]*)\)(\?)?', expr)
    if not m:
        return [expr]
    alts = m.group(1).split('|')
    if m.group(2):                       # (x)? 允许整组缺席
        alts = alts + ['']
    out = []
    for a in alts:
        out.extend(_expand(expr[:m.start()] + a + expr[m.end():]))
    return out


def _rewrite_hosts(patterns):
    r"""从重写 / 脚本规则里提取域名，用于核对 MITM 覆盖。

    输入形如 ^https?://(www\.)?g\.cn https://www.google.com 302
    只取第一段（匹配模式），剥掉正则转义与协议前缀后得到 g.cn。
    含分组的先展开，再逐个校验；仍带正则元字符的会显式告警而非丢弃。
    """
    hosts = set()
    for pat in patterns:
        first = str(pat).split()[0]
        s = first.replace(chr(92), "")          # 去掉正则转义反斜杠
        s = s.lstrip("^")
        if "://" in s:
            s = s.split("://", 1)[1]
        s = s.split("/")[0]                     # 先切掉路径，再处理分组
        for cand in _expand(s):
            cand = cand.strip(".")
            if not cand:
                continue
            if re.search(r'[()|?*+\[\]{}^$]', cand):
                warn('无法从 pattern 解析出域名，MITM 覆盖未被核对: %s' % first)
                continue
            if "." in cand:
                hosts.add(cand)
    return hosts


def _mitm_covers(mitm, host):
    for entry in mitm:
        e = entry.strip().strip('"')
        if e == host:
            return True
        if e.startswith('*.') and (host == e[2:] or host.endswith(e[1:])):
            return True
    return False


def check_rewrite_mitm(cfg):
    """重写规则涉及的域名必须在 MITM 列表里，否则规则从不触发。

    这正是本仓库删掉过又加回来的那两条 g.cn 重写：当初 hostname 为空，
    规则是死代码。只看「MITM enable = true」不够，必须逐域名核对。
    """
    # --- Shadowrocket ---
    inside = None
    rewrites, scripts, mitm_hosts, enabled = [], [], [], False
    for line in cfg.splitlines():
        s = line.strip()
        if s.startswith('[') and s.endswith(']'):
            inside = s
            continue
        if not s or s.startswith('#'):
            continue
        if inside == '[URL Rewrite]':
            rewrites.append(s)
        elif inside == '[Script]':
            # 脚本的 pattern= 与重写规则同理：域名不在 MITM 列表里就永不触发
            m = re.search(r'pattern=(\S+?)(?:,|$)', s)
            if m:
                scripts.append(m.group(1))
        elif inside == '[MITM]':
            if s.startswith('hostname'):
                mitm_hosts = [x.strip() for x in s.split('=', 1)[1].split(',') if x.strip()]
            elif s.startswith('enable'):
                enabled = s.split('=', 1)[1].strip().lower() == 'true'
    if rewrites and not enabled:
        fail('[URL Rewrite] 有规则但 [MITM] enable 不为 true，规则不会触发')
    for host in _rewrite_hosts(rewrites):
        if not _mitm_covers(mitm_hosts, host):
            fail('[URL Rewrite] 涉及 %s，但它不在 [MITM] hostname 列表里，规则是死代码'
                 % host)

    if scripts and not enabled:
        fail('[Script] 有脚本但 [MITM] enable 不为 true，脚本不会触发')
    for host in _rewrite_hosts(scripts):
        if not _mitm_covers(mitm_hosts, host):
            fail('[Script] 的 pattern 涉及 %s，但它不在 [MITM] hostname 列表里，脚本是死代码'
                 % host)

    # --- Stash ---
    if not os.path.exists(STASH_OVERRIDE):
        return
    try:
        import yaml
        doc = yaml.safe_load(io.open(STASH_OVERRIDE, encoding='utf-8').read())
    except Exception:
        return
    http = (doc or {}).get('http') or {}
    smitm = http.get('mitm') or []
    for host in _rewrite_hosts(http.get('url-rewrite') or []):
        if not _mitm_covers(smitm, host):
            fail('Stash url-rewrite 涉及 %s，但不在 http.mitm 列表里，规则是死代码'
                 % host)


# fake-ip-filter 里允许出现的策略。其余一律视为「走代理」。
FAKEIP_OK_POLICIES = {'DIRECT', '🎯 全球直连',
                      '🇨🇳 国内服务',
                      '🌏 国内媒体',
                      '🛑 广告拦截',
                      '🍃 应用净化'}


def _first_policy(cfg, host):
    """按 [Rule] 的先后顺序返回 host 命中的第一条策略，没命中返回 None。

    必须逐行按序判断：内联规则写在规则集之前，正是靠顺序压过上游归类。
    """
    if not hasattr(_first_policy, 'cache'):
        idx = {}
        for rtype, value, name in load_rules():
            idx.setdefault(name, []).append((rtype, value))
        lines = []
        for sec, i, line in sections(cfg):
            if sec != '[Rule]':
                continue
            m = re.match(r'RULE-SET,\S+/rule/(\S+?)\.list,(.+)$', line)
            if m:
                lines.append(('set', m.group(1), m.group(2).strip()))
                continue
            parts = [x.strip() for x in line.split(',')]
            if len(parts) >= 3 and parts[0].startswith('DOMAIN'):
                lines.append(('one', (parts[0], parts[1]), parts[2]))
        _first_policy.cache = (idx, lines)
    idx, lines = _first_policy.cache
    for kind, payload, policy in lines:
        if kind == 'one':
            if matches(payload[0], payload[1], host):
                return policy
        else:
            for rtype, value in idx.get(payload, ()):
                if matches(rtype, value, host):
                    return policy
    return None


def check_must_route(cfg):
    """_first_policy 只模拟域名类规则与规则集里的域名条目，GEOIP / IP-CIDR / FINAL
    不在模拟内。GEOIP,CN 这半行另外核对它的策略与 no-resolve，位置由 ORDER 管。"""
    for host, want in MUST_ROUTE.items():
        got = _first_policy(cfg, host)
        if got != want:
            fail('分流与预期不符: %s -> %s（应为 %s）' % (host, got, want))
    geo = [s for sec, i, s in sections(cfg) if sec == '[Rule]' and s.startswith('GEOIP,CN,')]
    if geo != ['GEOIP,CN,🇨🇳 国内服务,no-resolve']:
        fail('GEOIP,CN 应恰好一条且为 GEOIP,CN,🇨🇳 国内服务,no-resolve，实际: %s' % geo)


def check_fakeip(cfg):
    """fake-ip-filter 只能收录走直连的域名。

    fake-ip 的作用是让域名不必本地解析就能进规则匹配，对走代理的域名这恰恰
    是优点：解析交给远端，本地不留痕。把代理域名写进 fake-ip-filter 反而
    强制了一次本地 DNS 查询——既泄漏域名，又让被污染的结果参与分流。
    所以这张表只该放「直连 + 需要真实 IP」的域名。
    """
    path = os.path.join(os.path.dirname(CONFIG), 'stash.stoverride')
    if not os.path.exists(path):
        return
    body = io.open(path, encoding='utf-8').read()
    if 'fake-ip-filter:' not in body:
        return
    blk = body.split('fake-ip-filter:', 1)[1].split('default-nameserver:', 1)[0]
    for line in blk.split(chr(10)):
        line = line.strip()
        if not line.startswith('- '):
            continue
        entry = line[2:].strip().strip('"')
        core = entry.lstrip('*+').lstrip('.')
        if '*' in core or '.' not in core:
            # 带内部通配的条目（time.*.com 这类）以前直接跳过，从没查过——
            # time.*.com 命中 iOS 的 time.apple.com，而它随 Apple 走代理。
            # 现在拿一组常见真实主机去撞，撞中的逐个核对策略。
            pat = entry.replace('+.', '*.')
            for host in WILDCARD_PROBES:
                if fnmatch.fnmatchcase(host, pat):
                    policy = _first_policy(cfg, host)
                    if policy is not None and policy not in FAKEIP_OK_POLICIES:
                        fail('fake-ip-filter 的通配 %s 命中走代理的 %s -> %s'
                             % (entry, host, policy))
            continue
        policy = _first_policy(cfg, core)
        if policy is not None and policy not in FAKEIP_OK_POLICIES:
            fail('fake-ip-filter 收录了走代理的域名: %s -> %s。'
                 '对代理域名 fake-ip 才是正解，写进这张表会多一次本地解析并泄漏域名'
                 % (entry, policy))


def _canon_realip(entry, clash):
    """把两种语法归一化后比较。Clash 的 +.x 等价于 Surge 的 x 与 *.x 两条。"""
    e = entry.strip().strip('"')
    if clash and e.startswith('+.'):
        return {e[2:], '*.' + e[2:]}
    return {e}


def check_realip_parity(cfg):
    """Shadowrocket 的 always-real-ip 必须与 Stash 的 fake-ip-filter 一致。

    两份配置是同一套分流策略的两个实现，这张「必须拿到真实 IP」的表若只改一边，
    症状是单端才出现的灰歌、校时失败、路由器后台打不开——最难归因的那类问题。
    """
    path = os.path.join(os.path.dirname(CONFIG), 'stash.stoverride')
    if not os.path.exists(path):
        return
    body = io.open(path, encoding='utf-8').read()
    if 'fake-ip-filter:' not in body:
        return
    blk = body.split('fake-ip-filter:', 1)[1].split('default-nameserver:', 1)[0]
    stash = set()
    roots = set()           # +.x 顺带收进的根域 x；Shadowrocket 侧只写 *.x 也算一致
    for line in blk.split(chr(10)):
        line = line.strip()
        if not line.startswith('- '):
            continue
        entry = line[2:].strip().strip('"')
        if entry.startswith('*.'):
            # Clash / mihomo 的 *. 只匹配一级子域，而 Shadowrocket 的 *. 是任意深度。
            # 逐字比对时两边写法相同、看不出差异：*.126.net 曾在 Stash 侧漏掉
            # m701.music.126.net，在 Shadowrocket 侧却没问题。
            fail('Stash fake-ip-filter 的 %s 用了 *.，只匹配一级子域，'
                 '与 Shadowrocket 的任意深度不一致；改写成 +.%s' % (entry, entry[2:]))
        if entry.startswith('+.'):
            roots.add(entry[2:])
        stash |= _canon_realip(entry, True)

    m = re.search(r'^always-real-ip\s*=\s*(.+)$', cfg, re.M)
    if not m:
        fail('config/default.conf 缺少 always-real-ip，'
             'Stash 侧已有 %d 条 fake-ip-filter，两端不一致' % len(stash))
        return
    sr = set()
    for x in m.group(1).split(','):
        if x.strip():
            sr |= _canon_realip(x, False)

    for x in sorted(stash - sr - roots):
        fail('always-real-ip 缺少 Stash fake-ip-filter 里的 %s' % x)
    for x in sorted(sr - stash):
        fail('always-real-ip 多出 Stash fake-ip-filter 没有的 %s' % x)


def check_modules():
    """module/ 下的自托管模块必须保持无脚本。

    这批模块的卖点就是「纯重写」：只按 URL 拦截，不下载也不执行远端 JS。
    上游哪天在里面塞进 script-path，同步会把它一并搬进来——那时解密后的
    明文流量就会交给第三方代码处理，而这正是选它的理由被推翻的时刻。
    """
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'module')
    if not os.path.isdir(d):
        return 0
    n = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(('.sgmodule', '.stoverride')):
            continue
        n += 1
        body = io.open(os.path.join(d, fn), encoding='utf-8').read()
        for marker in ('script-path', '[Script]'):
            if marker in body:
                fail('module/%s 出现 %s——自托管模块应保持纯重写，'
                     '引入远端脚本等于把明文流量交给第三方代码' % (fn, marker))
    return n


def check_clash():
    """校验 config/clash.yaml：语法、引用完整性、协议分组的类型名写法。

    协议分组那一项是本仓库踩过的坑的延续：Stash 上 exclude-type 被静默忽略，
    Hy2 混进 VLESS 组，是在设备上用出来才发现的。mihomo 实现了它，但
    group 层比较的是 AdapterType.String()（Vless / Shadowsocks），
    不是配置里的 type: 值（vless / ss）。写成 ss 排不掉 SS 节点，
    而且同样不报错——又是一次静默失效。
    """
    path = os.path.join(os.path.dirname(CONFIG), 'clash.yaml')
    if not os.path.exists(path):
        return 0
    try:
        import yaml
    except ImportError:
        warn('未安装 PyYAML，跳过 clash.yaml 校验')
        return 0
    try:
        d = yaml.safe_load(io.open(path, encoding='utf-8').read())
    except Exception as e:                                   # noqa: BLE001
        fail('config/clash.yaml 不是合法 YAML: %s' % e)
        return 0

    check_controller(d)
    check_listeners(d)
    bc = _tool('build-clash.py')
    check_clash_ipv6(d)
    check_clash_proxy_providers(d, bc)

    groups = d.get('proxy-groups') or []
    names = [g.get('name') for g in groups]
    dup = {x for x in names if names.count(x) > 1}
    for x in sorted(dup):
        fail('clash.yaml 有同名分组: %s' % x)
    nameset = set(names)
    builtin = {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'}

    for g in groups:
        for pr in g.get('proxies') or []:
            if pr not in nameset and pr not in builtin:
                fail('clash.yaml 分组 %s 引用了不存在的策略 %s' % (g.get('name'), pr))

    rp = d.get('rule-providers') or {}
    providers = set(rp)
    root = os.path.dirname(os.path.dirname(CONFIG))
    for pname, prov in sorted(rp.items()):
        if not os.path.exists(os.path.join(root, 'stash', pname + '.txt')):
            fail('clash.yaml 的 rule-provider %s 没有对应的 stash/%s.txt'
                 % (pname, pname))
        # iOS 的 Network Extension 只有约 50 MiB 可用（clash.md 的配置最佳实践），大型
        # text 规则集在设备上解析建表可能把它挤爆。domain / ipcidr 一律走 MRS；
        # MRS 不支持 classical，改了后缀也不行。
        behavior, fmt = prov.get('behavior'), prov.get('format')
        if behavior in ('domain', 'ipcidr') and fmt != 'mrs':
            fail('clash.yaml 的 rule-provider %s 是 %s 却用 format: %s，'
                 '应改用 MRS（tools/build-mrs.py）' % (pname, behavior, fmt))
        elif fmt == 'mrs':
            if behavior not in ('domain', 'ipcidr'):
                fail('clash.yaml 的 rule-provider %s 用 MRS，但 behavior 是 %s——'
                     'MRS 只支持 domain / ipcidr' % (pname, behavior))
            if not os.path.exists(os.path.join(root, 'mrs', pname + '.mrs')):
                fail('clash.yaml 的 rule-provider %s 没有对应的 mrs/%s.mrs'
                     '（先跑 tools/build-mrs.py）' % (pname, pname))
            if not str(prov.get('url') or '').endswith('/mrs/%s.mrs' % pname):
                fail('clash.yaml 的 rule-provider %s 的 url 没有指向 mrs/%s.mrs'
                     % (pname, pname))
        # classical 一律内嵌，且内容必须就是 stash/ 源文件去掉 USER-AGENT 之后的那些行。
        # 内嵌的规则不会自己更新，手改一条就永久偏离源文件，所以逐条比对。
        if behavior == 'classical':
            want = bc.classical_payload(pname[:-len('-classical')]) \
                if pname.endswith('-classical') else None
            if prov.get('type') != 'inline':
                fail('clash.yaml 的 rule-provider %s 是 classical 却不是 type: inline——'
                     'classical 一律内嵌，不为它另开远程规则集' % pname)
            elif want is not None and [str(x) for x in prov.get('payload') or []] != want:
                fail('clash.yaml 的 rule-provider %s 内嵌的规则与 stash/%s.txt 不一致'
                     '（应为去掉 USER-AGENT 后的 %d 条），请重新运行 tools/build-clash.py'
                     % (pname, pname, len(want)))
            elif not prov.get('payload'):
                fail('clash.yaml 的 rule-provider %s 内嵌了空的 payload' % pname)

    subs = d.get('sub-rules') or {}

    def check_rule(r, where):
        parts = [x.strip() for x in str(r).split(',')]
        if parts[0] == 'SUB-RULE':
            # 形如 SUB-RULE,(条件),子规则名：目标是子规则，不是策略
            if parts[-1] not in subs:
                fail('clash.yaml %s引用了不存在的子规则: %s' % (where, parts[-1]))
            return
        if parts[0] == 'RULE-SET' and parts[1] not in providers:
            fail('clash.yaml %s引用了未定义的 rule-provider: %s' % (where, parts[1]))
        if parts[0] == 'MATCH':
            tgt = parts[1]
        elif parts[0] in ('AND', 'OR', 'NOT'):
            tgt = parts[-1]             # 逻辑规则的条件里有逗号，策略在最后
        else:
            tgt = parts[2] if len(parts) > 2 else None
        if tgt and tgt not in nameset and tgt not in builtin:
            fail('clash.yaml %s指向了不存在的策略: %s' % (where, tgt))

    for r in d.get('rules') or []:
        check_rule(r, '规则')
    for sname, srules in sorted(subs.items()):
        if not srules:
            fail('clash.yaml 的子规则 %s 是空的' % sname)
        for r in srules or []:
            check_rule(r, '子规则 %s ' % sname)

    # exclude-type 必须用 AdapterType 名。小写的 ss / vless 之类会静默失效。
    #
    # 直接从 build-clash.py 取 ALL_TYPES / BUILTIN_TYPES，不在这里维护第二份拷贝——
    # 这份拷贝曾经就是漂移源：build-clash.py 补了 8 个新协议后，
    # 这里还是旧的 15 个，于是把合法的 Masque / Tailscale 判成非法。
    # 上游新增协议由 tools/sync-modules.py 的 check_type_drift 负责发现。
    adapter_types = set(bc.ALL_TYPES) | set(bc.BUILTIN_TYPES)
    for g in groups:
        et = g.get('exclude-type')
        have = set(str(et).split('|')) if et else set()
        for t in sorted(have):
            if t not in adapter_types:
                fail('clash.yaml 分组 %s 的 exclude-type 含 %s，'
                     '不是 AdapterType 名（应写 Shadowsocks 而非 ss），会静默失效'
                     % (g.get('name'), t))
        # include-all 会把订阅里用 type: direct / dns / reject / rematch 声明的内置出站
        # 一并收进来。直连伪节点延迟最低，进了 url-test 组会被选中，整组流量悄悄直连。
        if g.get('include-all') or g.get('include-all-providers'):
            miss = [t for t in bc.BUILTIN_TYPES if t not in have]
            if miss:
                fail('clash.yaml 分组 %s 用了 include-all 却没有用 exclude-type 排除内置出站 %s：'
                     '订阅里的 type: direct 一类条目会被当成节点收进来'
                     % (g.get('name'), '|'.join(miss)))
    return len(groups)


def check_clash_ipv6(d):
    """顶层 ipv6 必须是 true，dns.ipv6 必须是 false。

    clash.md「TUN 与网络接入」：ipv6 为 false 时隧道不接管 IPv6。在有 IPv6 的网络上，
    直接用 IPv6 地址发起的连接整个绕过 Clash——不匹配规则、不走代理、不拦截。
    「关掉 IPv6」的直觉在这里恰好相反；这份配置曾经就写着 ipv6: false。
    不让 App 用 IPv6 靠的是 dns.ipv6: false（不回 AAAA），所以两项要一起守住。
    """
    if d.get('ipv6') is not True:
        fail('clash.yaml 的顶层 ipv6 不是 true：Clash for Apple 的隧道就不接管 IPv6，'
             '直接用 IPv6 地址的连接会整个绕过规则与代理')
    if (d.get('dns') or {}).get('ipv6') is not False:
        fail('clash.yaml 的 dns.ipv6 不是 false：App 会拿到 AAAA，流量改走 IPv6 目标，'
             '而多数机场节点的 IPv6 出口不完整')


def check_clash_proxy_providers(d, bc):
    """模板里启用的订阅必须带 proxy 与 exclude-filter。

    proxy：不写时订阅更新按规则走，落到 🐟 漏网之鱼 → 🚀 节点选择，等于用这家机场
    的节点更新这家机场的订阅，节点全挂后就再也更新不了（clash.md「代理集合」）。
    exclude-filter：信息类伪节点在集合层就丢掉；词表只有 build-stash.py 那一份。
    """
    want = '(?i)(' + bc.bs.PSEUDO_WORDS + ')'
    for name, prov in sorted((d.get('proxy-providers') or {}).items()):
        if not prov.get('proxy'):
            fail('clash.yaml 的 proxy-provider %s 没写 proxy：订阅更新会按规则经它自己的'
                 '节点发出，节点全挂时无法更新' % name)
        if prov.get('exclude-filter') != want:
            fail('clash.yaml 的 proxy-provider %s 的 exclude-filter 与 build-stash.py 的 '
                 'PSEUDO_WORDS 不一致，伪节点会漏进分组' % name)


def check_controller(d):
    """外部控制器的危险配置必须拦下来。

    mihomo 的鉴权中间件写作 if secret != "" { r.Use(authentication(secret)) }
    —— secret 为空就完全不挂载鉴权，API 对局域网全开。而 PUT /configs
    一个请求即可替换整份配置，等于把设备全部流量导向任意服务器，
    不需要代码执行。所以「开了控制器但没有 secret」是本仓库最危险的
    一种配置，判 FAIL 而不是 WARN。

    CORS 同理：mihomo 默认 allow-origins ["*"] + allow-private-network true，
    配上空 secret 后，设备访问的任何网站都能用 JS 在后台驱动控制器。
    """
    ec = d.get('external-controller')
    if not ec:
        return
    secret = d.get('secret')
    if not secret or not str(secret).strip():
        fail('clash.yaml 开了 external-controller 但 secret 为空，'
             'mihomo 会完全不挂载鉴权，API 对局域网全开')
    elif str(secret).startswith('CHANGE-ME'):
        warn('clash.yaml 的 secret 仍是占位符，导入前必须换成随机串'
             '（python -c "import secrets;print(secrets.token_urlsafe(32))"）')
    elif len(str(secret)) < 24:
        fail('clash.yaml 的 secret 只有 %d 字符，太短。控制器绑在局域网时'
             '它是唯一的保护' % len(str(secret)))

    cors = d.get('external-controller-cors') or {}
    origins = cors.get('allow-origins')
    if isinstance(origins, list) and '*' in origins:
        fail('clash.yaml 的 external-controller-cors.allow-origins 含 *，'
             '任何网页都能跨域驱动控制器')
    if cors.get('allow-private-network') is True:
        warn('clash.yaml 的 allow-private-network 为 true —— '
             '设备访问的任何网站都能用 JS 触达这个控制器。'
             '只有要用浏览器面板时才需要，且应同时收紧 allow-origins')
    if str(ec).startswith('0.0.0.0') or str(ec).startswith('[::]'):
        warn('clash.yaml 的 external-controller 绑在 %s（局域网可达）。'
             'mihomo 没有针对控制器的来源 IP 白名单，'
             '只在可信网络下使用' % ec)


def check_listeners(d):
    """公开模板不许替用户开任何对局域网的监听。

    clash.md「配置与访问保护」：正常连接不需要本地监听；dns.listen 是独立的 DNS 服务，
    代理认证、allow-lan、控制器 secret 都不保护它。照抄模板的人不会去改这些，
    绑在 0.0.0.0 上就等于替他们对同一个 Wi-Fi 开了一个没有鉴权的解析器。
    """
    listen = str((d.get('dns') or {}).get('listen') or '')
    if listen and not re.match(r'(127\.|\[::1\]|localhost)', listen):
        fail('clash.yaml 的 dns.listen 绑在 %s（局域网可达），这个 DNS 服务没有任何鉴权'
             % listen)
    if d.get('allow-lan') is True:
        fail('clash.yaml 开了 allow-lan，公开模板不应替用户开放局域网代理')


# 直连组必须带的中转排除子句，以及绝不能出现的正向写法。
RELAY_EXCLUDE_CLAUSE = '(?!.*(?<![A-Za-z])CTCU(?![A-Za-z]))'
RELAY_AS_POSITIVE = re.compile(r'\(\?=\.\*[^)]*CTCU')


def check_direct_groups(cfg):
    """三份配置里的「直连」分组必须排除中转，且不能把中转标签当正向条件。

    #1 引入的 🇺🇲 美国直连 写成 (?=.*CTCU)——把中转标签当成直连标志，
    选中的 5 个节点全是 Cloudflare 中转，而它随即被设成节点选择与 AI 服务的首选。
    节点名看不出对错，真正的核对要靠 tools/check-direct.py 读订阅文件的
    network 字段；那份文件含凭据不能进仓库，所以 CI 里只能守住结构。
    """
    found = []
    for m in re.finditer(r'^(\S+ [^=\n]*直连) = url-test, policy-regex-filter = (.*?), url =',
                         cfg, re.M):
        found.append(('Shadowrocket', m.group(1).strip(), m.group(2)))
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml:
        for fname, label in (('stash.stoverride', 'Stash'), ('clash.yaml', 'Clash')):
            path = os.path.join(os.path.dirname(CONFIG), fname)
            if not os.path.exists(path):
                continue
            doc = yaml.safe_load(io.open(path, encoding='utf-8').read()
                                 .replace('#!replace', '')) or {}
            for g in doc.get('proxy-groups') or []:
                if '直连' in g.get('name', '') and g.get('filter'):
                    found.append((label, g['name'], g['filter']))
    for label, name, rx in found:
        if RELAY_EXCLUDE_CLAUSE not in rx:
            fail('%s 的 %s 缺少中转排除 %s，会把 CF 中转节点选进「直连」组'
                 % (label, name, RELAY_EXCLUDE_CLAUSE))
        if RELAY_AS_POSITIVE.search(rx):
            fail('%s 的 %s 把 CTCU 写成了正向条件 (?=.*CTCU)——'
                 'CTCU 是中转标签，这样选出来的全是中转' % (label, name))
    names = {}
    for label, name, rx in found:
        names.setdefault(label, set()).add(name)
    if len({frozenset(v) for v in names.values()}) > 1:
        fail('三份配置的直连分组不一致: %s'
             % '; '.join('%s=%s' % (k, sorted(v)) for k, v in sorted(names.items())))
    return len(found)


def _clash_only_rules(got, want, doc):
    """核对 Clash 独有的三处，返回 (剥掉它们之后的规则, 相应的期望)。

    Clash 与 default.conf 的差异只允许是 build-clash.py 里声明的这三处：
      1. 第一条是 QUIC_ENTRY，把 UDP/443 交给 sub-rules 里的镜像；
      2. GEOSITE_RULE 恰好一条，紧挨在 GEOIP,CN 之前；
      3. 剔掉 USER-AGENT 后为空的 classical 集不引用。
    其余任何多出或缺少的规则，仍由调用方按「与 default.conf 不一致」报出来。
    镜像这里独立重算一遍（按 clash.yaml 里各组的第一候选），不调用生成器的同名函数。
    """
    bc = _tool('build-clash.py')
    kinds = bc.clash_kinds()
    dropped = {slug + '-classical' for slug, ks in kinds.items()
               if 'classical' not in ks
               and os.path.exists(os.path.join(STASH_DIR, slug + '-classical.txt'))}
    expect = [r for r in want
              if not (r.startswith('RULE-SET,') and r.split(',')[1] in dropped)]

    main = list(got)
    if not got or got[0] != bc.QUIC_ENTRY:
        fail('Clash 的第一条规则应是 QUIC 子规则入口 %s，实际是 %s'
             % (bc.QUIC_ENTRY, got[0] if got else '（空）'))
    else:
        got = got[1:]
        main = list(got)
    if bc.QUIC_ENTRY in got:
        fail('Clash 的规则里出现了第二条 %s' % bc.QUIC_ENTRY)

    if got.count(bc.GEOSITE_RULE) != 1:
        fail('Clash 的规则里 %s 应恰好一条，实际 %d 条'
             % (bc.GEOSITE_RULE, got.count(bc.GEOSITE_RULE)))
    else:
        i = got.index(bc.GEOSITE_RULE)
        if got[i + 1:i + 2] != [bc.GEOSITE_BEFORE]:
            fail('Clash 的 %s 必须紧挨在 %s 之前（域名层的最后兜底，不能抢在显式规则前面）'
                 % (bc.GEOSITE_RULE, bc.GEOSITE_BEFORE))
        got = got[:i] + got[i + 1:]

    subs = doc.get('sub-rules') or {}
    if set(subs) != {bc.QUIC_SUBRULE}:
        fail('Clash 的 sub-rules 应当只有 %s，实际: %s' % (bc.QUIC_SUBRULE, sorted(subs)))
    mirror = [str(r) for r in subs.get(bc.QUIC_SUBRULE) or []]
    first = {g['name']: (g.get('proxies') or [None])[0]
             for g in doc.get('proxy-groups') or []}
    if len(mirror) != len(main):
        fail('Clash 的 QUIC 子规则有 %d 条，主规则（不含入口）有 %d 条——镜像必须逐条对应'
             % (len(mirror), len(main)))
    else:
        for a, b in zip(main, mirror):
            head, pol, mods = bc.split_policy(a)
            keep = pol in bc.KEEP_POLICIES or first.get(pol) in bc.KEEP_POLICIES
            should = ','.join(head + [pol if keep else 'REJECT'] + mods)
            if b != should:
                fail('Clash 的 QUIC 子规则与主规则不对应：%s 的镜像是 %s，应为 %s'
                     '（默认走代理的换 REJECT，默认直连 / 拦截的照旧）' % (a, b, should))
                break
    return got, expect


def check_derived_parity():
    """Stash / Clash 的规则与业务组默认出口必须与 default.conf 一致。

    两个生成器现在从 default.conf 推导，按构造就一致；这项检查防的是有人手改
    生成物，或者往生成器里重新塞回手写规则。2026-09 之前正是手写拷贝导致
    Stash / Clash 把 .cn 与 apple.com 排在广告规则集之前，3402 个广告域名放行。
    Clash 另有三处声明过的独有规则，见 _clash_only_rules。
    """
    try:
        import yaml
    except ImportError:
        warn('未安装 PyYAML，跳过派生一致性检查')
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'bs', os.path.join(ROOT, 'tools', 'build-stash.py'))
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)

    kinds = {}
    for f in glob.glob(os.path.join(ROOT, 'stash', '*.txt')):
        slug, kind = os.path.basename(f)[:-4].rsplit('-', 1)
        kinds.setdefault(slug, []).append(kind)
    for slug in kinds:
        kinds[slug].sort(key=['domain', 'ipcidr', 'classical'].index)
    want = []
    bs.emit_rules(want.append, kinds, indent='')
    want = [x[2:] for x in want if x.startswith('- ')]

    sel = bs.sr_select_groups()
    for fname, label in (('stash.stoverride', 'Stash'), ('clash.yaml', 'Clash')):
        path = os.path.join(os.path.dirname(CONFIG), fname)
        if not os.path.exists(path):
            continue
        doc = yaml.safe_load(io.open(path, encoding='utf-8').read()
                             .replace('#!replace', '')) or {}
        got = [str(r) for r in doc.get('rules') or []]
        expect = want
        if label == 'Clash':
            # 先逐项核对并剥掉 Clash 独有的三处，剩下的必须与 default.conf 逐条相同。
            got, expect = _clash_only_rules(got, want, doc)
        if got != expect:
            i = next((k for k, (a, b) in enumerate(zip(got, expect)) if a != b),
                     min(len(got), len(expect)))
            fail('%s 的规则与 default.conf [Rule] 不一致（共 %d / 应为 %d 条），'
                 '第 %d 条起不同：%s ≠ %s' % (label, len(got), len(expect), i + 1,
                                           got[i] if i < len(got) else '（缺）',
                                           expect[i] if i < len(expect) else '（多）'))
        groups = {g['name']: g for g in doc.get('proxy-groups') or []}
        for name, (cands, psn) in sel.items():
            g = groups.get(name)
            if g is None:
                fail('%s 缺少 default.conf 里的分组 %s' % (label, name))
                continue
            first = (g.get('proxies') or [None])[0]
            expect = psn or next((c for c in cands if c in groups or c in
                                  ('DIRECT', 'REJECT', 'REJECT-DROP')), None)
            if first != expect:
                fail('%s 的 %s 默认出口是 %s，default.conf 是 %s' % (label, name, first, expect))


def check_pseudo_parity(cfg):
    """default.conf 各组手写的伪节点排除词，必须覆盖 build-stash.py 的那一份词表。

    Stash / Clash 的 filter 由生成器拼出来，Shadowrocket 的 policy-regex-filter 是手写的
    同一批词。以前生成器里就有两份互相漏词的表（自动组没有「官網」，地区组与 Clash 的
    协议组没有「客服 / 群组 / 网站」），而 default.conf 照着各抄了一份，谁也没发现。
    按名筛选的组都要带基础词表 PSEUDO_WORDS；没有正向条件 (?= 的那几个
    （♻️ / 🔯 / 🔮 / 🔧）另加 EXCLUDE_EXTRA。
    """
    bs = _tool('build-stash.py')
    base, extra = bs.PSEUDO_WORDS.split('|'), bs.EXCLUDE_EXTRA.split('|')
    n = 0
    for sec, i, s in sections(cfg):
        if sec != '[Proxy Group]' or 'policy-regex-filter' not in s:
            continue
        name = s.split('=')[0].strip()
        rx = s.split('policy-regex-filter', 1)[1]
        words = base + ([] if '(?=' in rx else extra)
        miss = [w for w in words if w not in rx]
        if miss:
            fail('L%d 分组 %s 的 policy-regex-filter 缺少伪节点排除词 %s'
                 '（词表在 tools/build-stash.py 的 PSEUDO_WORDS / EXCLUDE_EXTRA）'
                 % (i, name, '|'.join(miss)))
        n += 1
    if not n:
        fail('default.conf 里没有找到任何带 policy-regex-filter 的分组，伪节点排除词无从核对')


def check_test_params(cfg):
    """自动测速组的 interval / tolerance 三份配置必须一致。

    Clash 那份曾经在生成器里各写各的：地区直连组 300 / 50，而 default.conf、Stash 与
    README 都是 600 / 200。🤖 AI 服务 默认落在 🇺🇲 美国直连 上，容差小了出口更常跳，
    正是「IP 稳定性」一节要避免的。数值现在只在 build-stash.py 的 AUTO_TEST /
    REGION_TEST 定义；这里拿 default.conf 的手写值去对两份生成物里的同名组。
    """
    try:
        import yaml
    except ImportError:
        warn('未安装 PyYAML，跳过测速参数一致性检查')
        return
    sr = {}
    for sec, i, s in sections(cfg):
        if sec != '[Proxy Group]':
            continue
        name, body = s.split('=', 1)
        if body.split(',')[0].strip() not in ('url-test', 'fallback', 'load-balance'):
            continue
        iv = re.search(r'\binterval\s*=\s*(\d+)', body)
        tol = re.search(r'\btolerance\s*=\s*(\d+)', body)
        sr[name.strip()] = (int(iv.group(1)) if iv else None,
                            int(tol.group(1)) if tol else None)
    for fname, label in (('stash.stoverride', 'Stash'), ('clash.yaml', 'Clash')):
        path = os.path.join(os.path.dirname(CONFIG), fname)
        if not os.path.exists(path):
            continue
        doc = yaml.safe_load(io.open(path, encoding='utf-8').read()
                             .replace('#!replace', '')) or {}
        seen = set()
        for g in doc.get('proxy-groups') or []:
            name = g.get('name')
            if name not in sr:
                continue
            seen.add(name)
            got = (g.get('interval'), g.get('tolerance'))
            if got != sr[name]:
                fail('%s 的 %s 测速参数是 interval=%s / tolerance=%s，default.conf 是 %s / %s'
                     % (label, name, got[0], got[1], sr[name][0], sr[name][1]))
        for name in sorted(set(sr) - seen):
            fail('%s 缺少 default.conf 里的自动测速组 %s' % (label, name))


def check_workflows():
    """GitHub 只在推送后才报 YAML 错误，本地必须先挡住。"""
    paths = sorted(glob.glob(os.path.join(ROOT, '.github', 'workflows', '*.yml'))
                   + glob.glob(os.path.join(ROOT, '.github', 'workflows', '*.yaml')))
    if not paths:
        return
    try:
        import yaml
    except ImportError:
        warn('PyYAML 未安装，跳过 workflow 语法校验（pip install pyyaml）')
        return
    for path in paths:
        rel = os.path.relpath(path, ROOT)
        try:
            doc = yaml.safe_load(io.open(path, encoding='utf-8').read())
        except yaml.YAMLError as exc:
            mark = getattr(exc, 'problem_mark', None)
            where = ' 第%d行' % (mark.line + 1) if mark else ''
            fail('%s YAML 语法错误%s: %s' % (rel, where, getattr(exc, 'problem', exc)))
            continue
        if not isinstance(doc, dict) or 'jobs' not in doc:
            fail('%s 缺少 jobs 段' % rel)


# 仓库是公开的：凭据一进提交就已经泄漏，删文件补救不了，只能轮换。
# GitHub 原生的 secret scanning 只认云厂商 / 访问令牌的格式，认不得代理分享链接、
# 机场订阅地址和 Shadowrocket 的 MITM 私钥，这里补这一层。
# 扫描范围是「这次提交会带上的文件」：已跟踪的，加上未跟踪却没被 .gitignore 挡住的，
# 所以提交前本地跑一遍就能拦住。*.local.conf 这类被忽略的文件不在内——
# 它们本来就是给真实凭据留的位置。
_PROXY_SCHEMES = 'ss|ssr|vmess|vless|trojan|hysteria2?|hy2|tuic|anytls|snell|wireguard'
_CONF_EXTS = ('.conf', '.yaml', '.yml', '.stoverride', '.sgmodule')
SECRET_LIST_DIRS = ('rule/', 'stash/')
SECRET_MAX_BYTES = 8 * 1024 * 1024
# 占位用的全零 UUID（tools/fixtures 里的测试节点用它）
SECRET_UUID_OK = {'00000000-0000-0000-0000-000000000000'}

# (标签, 正则, 规则集目录里也扫, 限定扩展名)
# 规则集里满是域名，UUID 形状的域名标签（xxxxxxxx-xxxx-….example.com）很常见，
# 只对不会误伤的几类扫。
SECRET_PATTERNS = [
    ('代理分享链接',
     re.compile(r'(?<![A-Za-z0-9+.\-])(?:%s)://[^\s\'"<>)\]]{8,}' % _PROXY_SCHEMES, re.I),
     True, None),
    ('URL 内嵌口令',
     re.compile(r'\b[a-z][a-z0-9+.\-]*://[^/\s:@\'"<>]+:(?![${<%])[^/\s@\'"<>]+@[^/\s\'"<>]+',
                re.I),
     True, None),
    ('MITM 私钥',
     re.compile(r'^[ \t]*ca-(?:p12|passphrase)[ \t]*=[ \t]*[^\s#]{6,}', re.I | re.M),
     True, None),
    ('私钥块',
     re.compile(r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
     True, None),
    ('控制器 secret',
     re.compile(r'^[ \t]*secret[ \t]*[:=][ \t]*["\']?(?!CHANGE-ME|<|\$)[^\s"\'#]{8,}',
                re.I | re.M),
     False, _CONF_EXTS),
    ('订阅 / 口令参数',
     re.compile(r'[?&](?:token|key|secret|password|passwd|auth|sub)=[A-Za-z0-9_\-+/=.]{12,}',
                re.I),
     False, None),
    ('UUID',
     re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I),
     False, None),
]


def _mask(frag):
    """CI 日志在公开仓库里是公开的，命中的内容不能原样打出来。"""
    if '://' in frag:
        return frag.split('://', 1)[0] + '://…'
    return frag[:4] + '…'


def scan_secrets(text, rel):
    """返回 [(标签, 行号, 脱敏片段)]。rel 是以 / 分隔的仓库相对路径。"""
    in_list = rel.startswith(SECRET_LIST_DIRS)
    ext = os.path.splitext(rel)[1].lower()
    hits = []
    for label, rx, lists_ok, exts in SECRET_PATTERNS:
        if (in_list and not lists_ok) or (exts and ext not in exts):
            continue
        for m in rx.finditer(text):
            frag = m.group(0)
            if label == 'UUID' and frag.lower() in SECRET_UUID_OK:
                continue
            hits.append((label, text.count('\n', 0, m.start()) + 1, _mask(frag)))
    return hits


def _secret_selftest():
    """扫描器自己坏了会静默放行一切，所以每次先用样本验一遍。

    样本一律拆开拼接：整串写在源码里，这个文件自己就会被扫出来。
    """
    j = ''.join
    positives = [
        ('代理分享链接', 'a.conf', j(['vm', 'ess://', 'QUJDREVGR0g', 'x' * 8])),
        ('代理分享链接', 'a.md', j(['ss', '://', 'YWVzLTEyOC1nY206dGVzdA', '@h.example:8388'])),
        ('URL 内嵌口令', 'a.md', j(['https://', 'user:Passw0rdX', '@example.org/x'])),
        ('MITM 私钥', 'a.conf', j(['ca-', 'p12 = ', 'MIIJ' * 6])),
        ('MITM 私钥', 'a.conf', j(['ca-', 'passphrase = ', 'abc123'])),
        ('私钥块', 'a.txt', j(['-----BEGIN ', 'RSA PRIVATE KEY-----'])),
        ('控制器 secret', 'a.yaml', j(['secret: ', '"a1b2c3d4e5f6"'])),
        ('订阅 / 口令参数', 'a.md', j(['https://h.example/sub?token=', 'A1b2C3d4E5f6G7'])),
        ('UUID', 'a.yaml', j(['uuid: ', '3f2c9a7e-5b1d-', '4e8a-9c6f-1a2b3c4d5e6f'])),
        ('代理分享链接', 'rule/a.list', j(['trojan', '://', 'pw@h.example:443'])),
    ]
    negatives = [
        ('a.md', 'https://github.com/adrianyusong/shadowrocket/blob/main/README.md'),
        ('a.yaml', 'secret: "CHANGE-ME-generate-a-random-secret"'),
        ('a.yaml', 'uuid: 00000000-0000-0000-0000-000000000000'),
        ('a.md', 'https://请替换.example/subscribe?flag=clash'),
        ('a.md', 'https://user:${TOKEN}@example.org/x'),
        ('rule/a.list', j(['DOMAIN,3f2c9a7e-5b1d-', '4e8a-9c6f-1a2b3c4d5e6f.t.example.net'])),
        ('a.py', 'secret = secrets.token_urlsafe(32)'),
        ('a.md', 'class://abcdefghijkl'),
        ('a.conf', '# ca-p12 与 ca-passphrase 是本机私钥，别入库'),
    ]
    problems = []
    for label, rel, text in positives:
        if label not in {h[0] for h in scan_secrets(text, rel)}:
            problems.append('该命中却没命中 [%s]（样本 %s）' % (label, rel))
    for rel, text in negatives:
        got = scan_secrets(text, rel)
        if got:
            problems.append('不该命中却命中了 %s（样本 %s）' % (got[0][0], rel))
    return problems


def _secret_candidates():
    """这次提交会带上的文件：已跟踪 + 未跟踪但未被 .gitignore 排除。"""
    try:
        out = subprocess.run(
            ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
        names = [n for n in out.decode('utf-8', 'replace').split('\0') if n]
    except (OSError, subprocess.CalledProcessError):
        # 没有 git（例如下载的源码压缩包）：遍历目录，跳过 .gitignore 里的本机文件名
        names = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'secrets')]
            for f in files:
                if '.local.' in f or '.private.' in f or f.startswith('subscription'):
                    continue
                names.append(os.path.relpath(os.path.join(base, f), ROOT).replace(os.sep, '/'))
    return sorted(set(names))


def check_secrets():
    problems = _secret_selftest()
    for p in problems:
        fail('凭据扫描器自检失败: %s' % p)
    if problems:
        return 0
    scanned = 0
    leaked = False
    for rel in _secret_candidates():
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path) or os.path.getsize(path) > SECRET_MAX_BYTES:
            continue
        with io.open(path, 'rb') as fh:
            data = fh.read()
        if b'\0' in data[:4096]:
            continue
        scanned += 1
        for label, line, frag in scan_secrets(data.decode('utf-8', 'replace'), rel):
            leaked = True
            fail('疑似凭据 [%s] %s:%d  %s' % (label, rel, line, frag))
    if leaked:
        fail('上面的内容若已推送过，一律视为已泄漏：先轮换机场订阅 / MITM 证书 / '
             'controller secret，再清理仓库——只删文件没有用')
    return scanned


STASH_OVERRIDE = os.path.join(ROOT, 'config', 'stash.stoverride')
STASH_DIR = os.path.join(ROOT, 'stash')
CLASH_BUILTIN = {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'}


# Stash 官方文档列出的 proxy-groups 选项。不在其中的会被静默忽略——
# exclude-type 就是这样：写了不报错，但分组里混进了本该排除的协议节点。
STASH_GROUP_KEYS = {
    "name", "type", "proxies", "interval", "lazy", "ssid-policy",
    "include-all", "filter", "strategy", "url", "tolerance", "timeout",
    "benchmark-url", "benchmark-timeout", "benchmark-disabled", "hidden", "icon",
}


def check_stash():
    """校验 Stash 覆写文件。与 Shadowrocket 配置分开，但共用 rule/*.list。"""
    if not os.path.exists(STASH_OVERRIDE):
        return 0, 0
    try:
        import yaml
    except ImportError:
        warn('PyYAML 未安装，跳过 Stash 覆写校验')
        return 0, 0
    try:
        doc = yaml.safe_load(io.open(STASH_OVERRIDE, encoding='utf-8').read())
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = ' 第%d行' % (mark.line + 1) if mark else ''
        fail('stash.stoverride YAML 语法错误%s: %s'
             % (where, getattr(exc, 'problem', exc)))
        return 0, 0

    groups = doc.get('proxy-groups') or []
    provs = doc.get('rule-providers') or {}
    rules = doc.get('rules') or []
    names = {g.get('name') for g in groups}

    for g in groups:
        for pxy in g.get('proxies', []) or []:
            if pxy not in names and pxy not in CLASH_BUILTIN:
                fail('Stash 分组 %s 引用未定义策略: %s' % (g.get('name'), pxy))

    used = set()
    for r in rules:
        parts = [x.strip() for x in str(r).split(',')]
        pol = parts[-2] if parts[-1] in MODIFIERS else parts[-1]
        used.add(pol)
        if pol not in names and pol not in CLASH_BUILTIN:
            fail('Stash 规则引用未定义策略: %s' % pol)
        if parts[0] == 'RULE-SET' and parts[1] not in provs:
            fail('Stash 规则引用不存在的规则集: %s' % parts[1])

    # filter / exclude-type 只对 use 引入的 provider 或 include-all 之后的
    # 全体节点生效。两者都没有时组内无节点可筛，在 App 里表现为空组——
    # 这正是首个 Stash 版本踩的坑：18 个分组全空。
    SOURCES = ("include-all", "include-all-proxies", "include-all-providers",
               "use", "proxies")
    for g in groups:
        if not ("filter" in g or "exclude-type" in g):
            continue
        if not any(g.get(k) for k in SOURCES):
            fail("Stash 分组 %s 用了 filter/exclude-type 却没有节点来源"
                 "（include-all / use / proxies），会是空组" % g.get("name"))

    # 未被 Stash 支持的选项不会报错，只会静默失效，属于最难查的一类问题。
    for g in groups:
        for key in g:
            if key not in STASH_GROUP_KEYS:
                fail("Stash 分组 %s 用了 Stash 未记载的选项 %s，"
                     "该选项会被静默忽略" % (g.get("name"), key))

    for n in names:
        referenced = n in used or any(n in (g.get('proxies') or []) for g in groups)
        if not referenced:
            fail('Stash 孤儿分组，无任何引用: %s' % n)

    # 规则集文件必须真实存在，否则 Stash 拉取时静默少一类规则
    for k, v in provs.items():
        fname = os.path.basename(v.get('url', ''))
        if not fname or not os.path.exists(os.path.join(STASH_DIR, fname)):
            fail('Stash 规则集文件缺失: stash/%s' % fname)

    # domain behavior 的文件不能含逗号，那说明混进了带类型的规则
    for k, v in provs.items():
        if v.get('behavior') != 'domain':
            continue
        path = os.path.join(STASH_DIR, os.path.basename(v.get('url', '')))
        if not os.path.exists(path):
            continue
        for ln in io.open(path, encoding='utf-8'):
            ln = ln.strip()
            if ln and not ln.startswith('#') and ',' in ln:
                fail('Stash %s 是 domain behavior，却含带类型的规则: %s' % (k, ln))
                break
    return len(groups), len(rules)



def main():
    # 同 build-stash.py：Windows 主控台默认 cp1252，印中文会抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    cfg = io.open(CONFIG, encoding='utf-8').read()
    ngroups, nrefs = check_policy_refs(cfg)
    check_case(cfg)
    check_select_name(cfg)
    check_order(cfg)
    check_rulesets_exist(cfg)
    check_workflows()
    nsecret = check_secrets()
    check_no_resolve()
    check_classical_types()
    check_skip_proxy(cfg)
    check_hijack_dns(cfg)
    check_rewrite_mitm(cfg)
    check_fakeip(cfg)
    check_must_route(cfg)
    check_realip_parity(cfg)
    nmod = check_modules()
    nclash = check_clash()
    ndirect = check_direct_groups(cfg)
    check_derived_parity()
    check_pseudo_parity(cfg)
    check_test_params(cfg)
    rules = load_rules()
    check_keywords(rules)
    check_no_block(rules, cfg)
    sgroups, srules = check_stash()

    print('Shadowrocket: 策略组 %d，策略引用 %d，规则 %d 条'
          % (ngroups, nrefs, len(rules)))
    if nmod:
        print('自托管模块:   %d 个' % nmod)
    if ndirect:
        print('直连分组:     %d 个（三份配置合计）' % ndirect)
    if nclash:
        print('Clash:        分组 %d' % nclash)
    if sgroups:
        print('Stash:        分组 %d，规则 %d 条' % (sgroups, srules))
    print('凭据扫描:     %d 个文件' % nsecret)
    for w in WARNS:
        print('  WARN  %s' % w)
    for f in FAILS:
        print('  FAIL  %s' % f)
    print('')
    print('%s（失败 %d，警告 %d）'
          % ('通过' if not FAILS else '未通过', len(FAILS), len(WARNS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
