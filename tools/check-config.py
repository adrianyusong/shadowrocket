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
    rules = load_rules()
    check_keywords(rules)
    check_no_block(rules, cfg)
    sgroups, srules = check_stash()

    print('Shadowrocket: 策略组 %d，策略引用 %d，规则 %d 条'
          % (ngroups, nrefs, len(rules)))
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
