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

用法：
    python tools/check-config.py
"""
import collections
import glob
import io
import os
import re
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
]

# 正常服务不该被任何拦截类策略命中
MUST_NOT_BLOCK = [
    'www.linkedin.com', 'media.licdn.cn', 'api.pinduoduo.com', 'www.qq.com',
    'api.github.com', 'api.openai.com', 'www.google.com', 'weatherkit.apple.com',
    'mobilegw.alipay.com', 'acs.m.taobao.com', 'www.youtube.com', 'gw.alicdn.com',
    'dispatcher.is.autonavi.com', 'steampipe.akamaized.net', 'javascript.info',
    'javadoc.io',
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
    return False


def check_no_block(rules, cfg):
    blockers = {'reject-ads', 'reject-privacy'}
    idx = [(t, v) for t, v, n in rules if n in blockers]
    for sec, i, s in sections(cfg):
        if sec == '[Rule]' and s.startswith(('DOMAIN,', 'DOMAIN-SUFFIX,')) and 'REJECT' in s:
            p = [x.strip() for x in s.split(',')]
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
SKIP_PROXY_ALLOWED_APPLE = {'captive.apple.com'}


def check_skip_proxy(cfg):
    for sec, i, s in sections(cfg):
        if not s.startswith('skip-proxy'):
            continue
        items = [x.strip() for x in s.split('=', 1)[1].split(',')]
        for item in items:
            low = item.lower().lstrip('*.')
            if 'apple' in low or 'icloud' in low:
                if item not in SKIP_PROXY_ALLOWED_APPLE:
                    fail('skip-proxy 含 Apple 域名，会绕过隧道使代理规则失效: %s' % item)


def check_no_resolve():
    """IP 类规则必须保留 no-resolve。

    上游 ChinaMax 的一万两千余条 IP-CIDR 都带该修饰符。丢掉后每条规则都会
    为域名请求触发一次本地 DNS 解析——既把域名泄漏给国内 DNS，又让被污染的
    解析结果把境外域名判成国内。sync-rules.py 曾因只取 (类型, 值) 而全部丢失。
    """
    path = os.path.join(RULEDIR, 'china.list')
    if not os.path.exists(path):
        return
    total = kept = 0
    for line in io.open(path, encoding='utf-8'):
        line = line.strip()
        if not line.startswith('IP-CIDR'):
            continue
        total += 1
        if 'no-resolve' in line:
            kept += 1
    if total and kept < total * 0.9:
        fail('china.list 的 IP-CIDR 规则大量缺失 no-resolve（%d/%d 保留），'
             '会为域名请求触发本地 DNS 解析' % (kept, total))


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
        # 含内部通配的条目（stun.*.* / time1.*.com）无法映射到具体域名，跳过
        if '*' in core or '.' not in core:
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
    for line in blk.split(chr(10)):
        line = line.strip()
        if line.startswith('- '):
            stash |= _canon_realip(line[2:], True)

    m = re.search(r'^always-real-ip\s*=\s*(.+)$', cfg, re.M)
    if not m:
        fail('config/default.conf 缺少 always-real-ip，'
             'Stash 侧已有 %d 条 fake-ip-filter，两端不一致' % len(stash))
        return
    sr = set()
    for x in m.group(1).split(','):
        if x.strip():
            sr |= _canon_realip(x, False)

    for x in sorted(stash - sr):
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

    providers = set(d.get('rule-providers') or {})
    for pname in providers:
        fn = os.path.join(os.path.dirname(os.path.dirname(CONFIG)), 'stash',
                          pname + '.txt')
        if not os.path.exists(fn):
            fail('clash.yaml 的 rule-provider %s 没有对应的 stash/%s.txt'
                 % (pname, pname))

    for r in d.get('rules') or []:
        parts = [x.strip() for x in str(r).split(',')]
        if parts[0] == 'RULE-SET' and parts[1] not in providers:
            fail('clash.yaml 规则引用了未定义的 rule-provider: %s' % parts[1])
        tgt = parts[1] if parts[0] == 'MATCH' else (
            parts[2] if len(parts) > 2 else None)
        if tgt and tgt not in nameset and tgt not in builtin:
            fail('clash.yaml 规则指向了不存在的策略: %s' % tgt)

    # exclude-type 必须用 AdapterType 名。小写的 ss / vless 之类会静默失效。
    adapter_types = {'Shadowsocks', 'ShadowsocksR', 'Snell', 'Socks5', 'Http',
                     'Vmess', 'Vless', 'Trojan', 'Hysteria', 'Hysteria2',
                     'WireGuard', 'Tuic', 'Ssh', 'Mieru', 'AnyTLS'}
    for g in groups:
        et = g.get('exclude-type')
        if not et:
            continue
        for t in str(et).split('|'):
            if t not in adapter_types:
                fail('clash.yaml 分组 %s 的 exclude-type 含 %s，'
                     '不是 AdapterType 名（应写 Shadowsocks 而非 ss），会静默失效'
                     % (g.get('name'), t))
    return len(groups)


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
    cfg = io.open(CONFIG, encoding='utf-8').read()
    ngroups, nrefs = check_policy_refs(cfg)
    check_case(cfg)
    check_select_name(cfg)
    check_order(cfg)
    check_rulesets_exist(cfg)
    check_workflows()
    check_no_resolve()
    check_skip_proxy(cfg)
    check_rewrite_mitm(cfg)
    check_fakeip(cfg)
    check_realip_parity(cfg)
    nmod = check_modules()
    nclash = check_clash()
    rules = load_rules()
    check_keywords(rules)
    check_no_block(rules, cfg)
    sgroups, srules = check_stash()

    print('Shadowrocket: 策略组 %d，策略引用 %d，规则 %d 条'
          % (ngroups, nrefs, len(rules)))
    if nmod:
        print('自托管模块:   %d 个' % nmod)
    if nclash:
        print('Clash:        分组 %d' % nclash)
    if sgroups:
        print('Stash:        分组 %d，规则 %d 条' % (sgroups, srules))
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
