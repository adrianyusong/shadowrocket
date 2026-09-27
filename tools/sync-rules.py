#!/usr/bin/env python3
"""把配置里引用的全部上游 RULE-SET 收编进本仓库。

数据来源是 tools/sources.txt，不是配置文件——规则集收编后配置里只剩
指向本仓库的 URL，无法再从配置反推上游。

做三件事：
  1. 拉取 tools/sources.txt 里的全部上游规则集
  2. 全局去重——同一条规则只保留首次出现的那个策略。「首次」按
     config/default.conf [Rule] 里 RULE-SET 的先后算（见 order_by_rule），
     与设备的匹配顺序一致。只处理完全相同的规则；关键词、宽后缀这类
     重叠而非重复的规则不去重，谁先命中仍由 [Rule] 顺序决定
     IP 类规则一律补 no-resolve
  3. 按策略合并，每个策略输出一个 rule/*.list

同时把 QuantumultX 的 HOST / HOST-SUFFIX / HOST-KEYWORD 归一化成
Shadowrocket 原生的 DOMAIN / DOMAIN-SUFFIX / DOMAIN-KEYWORD，
消除跨 flavor 语法能否被解析的不确定性。

用法：
    python tools/sync-rules.py            # 拉取并写入 rule/
    python tools/sync-rules.py --dry-run  # 只报告，不写文件
"""
import argparse
import collections
import concurrent.futures as futures
import io
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, 'tools', 'sources.txt')
OUTDIR = os.path.join(ROOT, 'rule')
EXCLUDE = os.path.join(ROOT, 'tools', 'exclude.txt')

# 策略名 -> 输出文件名。策略名含 emoji 与中文，不能直接做文件名。
SLUG = {
    'DIRECT': 'direct',
    '🛑 广告拦截': 'reject-ads',
    '🍃 应用净化': 'reject-privacy',
    '🤖 AI 服务': 'ai',
    '📹 YOUTUBE': 'youtube',
    '🎥 NETFLIX': 'netflix',
    '🎬 DISNEY+': 'disney',
    '🎦 HBO': 'hbo',
    '📦 PRIMEVIDEO': 'primevideo',
    '🎧 SPOTIFY': 'spotify',
    '🎶 TIKTOK': 'tiktok',
    '🕹️ 巴哈姆特': 'bahamut',
    '📺 ABEMATV': 'abematv',
    '🎙️ TWITCH': 'twitch',
    '📀 EMBY': 'emby',
    '☁️ PIKPAK': 'pikpak',
    '🌍 国外媒体': 'media-global',
    '🌏 国内媒体': 'media-cn',
    '📲 TELEGRAM': 'telegram',
    '🐦 TWITTER': 'twitter',
    '📘 META': 'meta',
    '💬 DISCORD': 'discord',
    '💚 LINE': 'line',
    '🐱 GITHUB': 'github',
    '💰 支付服务': 'payment',
    '🎯 全球直连': 'direct-global',
    '🎮 游戏平台': 'game',
    '📢 谷歌服务': 'google',
    '🔍 BING': 'bing',
    'Ⓜ️ 微软服务': 'microsoft',
    '🎵 苹果媒体': 'apple-media',
    '🍎 苹果服务': 'apple',
    '🇨🇳 国内服务': 'china',
    '🚀 节点选择': 'proxy',
}

# QuantumultX 语法 -> Shadowrocket 原生语法
NORMALIZE = {
    'HOST': 'DOMAIN',
    'HOST-SUFFIX': 'DOMAIN-SUFFIX',
    'HOST-KEYWORD': 'DOMAIN-KEYWORD',
}

# 本仓库自维护的列表不参与收编，它们本来就在仓库里
# 规则尾部的修饰符。no-resolve 让 IP 类规则跳过域名请求，不触发本地 DNS 查询。
MODIFIERS = {"no-resolve", "extended-matching", "pre-matching", "force-remote-dns"}

# 会按 IP 匹配的规则类型。它们不带 no-resolve 时，遇到域名请求会先做本地解析。
IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR", "IP-ASN", "GEOIP"}

SELF_HOSTED = 'adrianyusong/shadowrocket'


def read_excludes():
    """读黑名单。返回 {(type, value): policy_slug or None}，None 表示所有策略。

    值以 *. 开头的是后缀模式（如 DOMAIN-SUFFIX,*.cn,game），由 excluded_by 处理。"""
    out = {}
    if not os.path.exists(EXCLUDE):
        return out
    for line in io.open(EXCLUDE, encoding='utf-8'):
        line = line.split('#')[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2:
            continue
        out[(parts[0], parts[1])] = parts[2] if len(parts) > 2 else None
    return out


def excluded_by(excludes, rtype, value, slug):
    """返回命中的黑名单键，没命中返回 None。先查精确条目，再查 *. 后缀模式。"""
    for key in [(rtype, value)] + [(rtype, '*' + value[i:])
                                   for i in range(len(value)) if value[i] == '.']:
        if key in excludes and excludes[key] in (None, slug):
            return key
    return None


def read_rulesets():
    """按 tools/sources.txt 中的顺序取出 (url, policy)。

    这个顺序只决定同一策略内多个上游的先后；跨策略的优先级由 order_by_rule
    按 [Rule] 重新排定。"""
    out = []
    for line in io.open(SOURCES, encoding='utf-8'):
        line = line.split('#')[0].strip()
        if not line:
            continue
        url, _, policy = line.partition(' ')
        policy = policy.strip()
        if not url.startswith('http') or not policy:
            continue
        if SELF_HOSTED in url:
            continue
        out.append((url, policy))
    return out


def order_by_rule(sets):
    """按 config/default.conf 的 [Rule] 里 RULE-SET 的先后给上游排序。

    去重是「首次出现者胜」。之前按 sources.txt 的顺序去重，而设备按 [Rule]
    的顺序匹配——两者有 181 对前后颠倒。于是同一条规则同时出现在两个上游时，
    它归哪个策略由 sources.txt 决定，你在 [Rule] 里排的优先级对它不起作用，
    文件头「按配置顺序全局去重」的说法也不成立。

    改为按 [Rule] 顺序后，重复规则的归属与设备实际的匹配顺序一致：[Rule] 里
    写在前面的策略拿走它。同一策略内的多个上游仍保持 sources.txt 的顺序
    （sort 是稳定的）。没在 [Rule] 里出现的策略排到最后。
    切换时实测 157710 条里有 66 条换了组，逐条核对出 snssdk.com（抖音与
    TikTok 共用）与 AppleTV 的 UA 需要例外；之后用真实日志重放又查出 digicert.com、
    akadns.net、xboxlive.com/.cn 三处。都已写进 exclude.txt。
    """
    cfg = io.open(os.path.join(ROOT, 'config', 'default.conf'), encoding='utf-8').read()
    order = [m.group(1) for m in re.finditer(r'^RULE-SET,\S+/rule/(\S+?)\.list,', cfg, re.M)]
    rank = {slug: i for i, slug in enumerate(order)}
    return sorted(sets, key=lambda s: rank.get(SLUG.get(s[1]), len(order)))


def fetch(url, attempts=3):
    for _ in range(attempts):
        try:
            return url, urllib.request.urlopen(url, timeout=60).read().decode('utf-8', 'ignore')
        except Exception:
            pass
    return url, None


def parse(body):
    """产出 (type, value, modifiers) 序列。

    修饰符必须保留：上游 ChinaMax 的 12163 条 IP-CIDR 都带 no-resolve，
    丢掉后每条规则都会为域名请求触发一次本地 DNS 解析——既把域名泄漏给
    国内 DNS，又让被污染的解析结果把境外域名判成国内（LinkedIn 即此类）。
    """
    for line in body.split(chr(10)):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        rtype = NORMALIZE.get(parts[0], parts[0])
        mods = tuple(x for x in parts[2:] if x in MODIFIERS)
        # IP 类规则一律补 no-resolve，不再依赖上游是否写了。
        # 只「保留」不够：Apple 源取的是 QuantumultX 格式，IP 行第三段是策略名、
        # 不带 no-resolve；proxy / china 集也各有几十条缺失（共 88 条）。
        # 缺一条就够：求值走到它时，域名会先交给本地（国内）DNS 解析以判断 IP 归属。
        # 泄漏范围取决于 Shadowrocket 的求值方式——严格逐行时，apple.list（[Rule]
        # 第一个带这种规则的集）之后所有没被前面域名规则接住的域名都会被解析；
        # 若按手册「域名类的规则优先于 IP 类规则」，则只有没有任何域名规则命中、
        # 最终落到 FINAL 的域名会被解析。两种情况下补上 no-resolve 都能堵住。
        if rtype in IP_TYPES and 'no-resolve' not in mods:
            mods = mods + ('no-resolve',)
        yield rtype, parts[1], mods

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sets = order_by_rule(read_rulesets())
    excludes = read_excludes()
    print('配置中引用的上游规则集: %d 个' % len(sets))
    print('黑名单条目: %d 条' % len(excludes))

    bodies = {}
    with futures.ThreadPoolExecutor(14) as pool:
        for url, body in pool.map(fetch, [u for u, _ in sets]):
            bodies[url] = body

    missing = [u.rsplit('/', 1)[-1] for u, b in bodies.items() if b is None]
    if missing:
        print('拉取失败，中止（避免写出残缺的规则）: %s' % ', '.join(missing))
        return 1

    seen = {}                                  # (type, value) -> policy
    merged = collections.OrderedDict()         # policy -> [(type, value)]
    stats = collections.Counter()
    dropped = collections.Counter()
    excluded = collections.Counter()
    total = 0

    for url, policy in sets:
        name = url.rsplit('/', 1)[-1].replace('.list', '')
        for rtype, value, mods in parse(bodies[url]):
            total += 1
            key = (rtype, value)
            hit = excluded_by(excludes, rtype, value, SLUG.get(policy))
            if hit:
                excluded[hit] += 1
                continue
            if key in seen:
                # 首次出现的策略优先，与自上而下匹配一致
                if seen[key] != policy:
                    dropped[(seen[key], policy)] += 1
                continue
            seen[key] = policy
            merged.setdefault(policy, []).append((rtype, value, mods))
            stats[name] += 1

    print('原始 %d 条 -> 去重后 %d 条（丢弃 %d 条，其中跨策略冲突 %d 条）'
          % (total, len(seen), total - len(seen) - sum(excluded.values()),
             sum(dropped.values())))
    if excluded:
        print('黑名单剔除 %d 条:' % sum(excluded.values()))
        for (t, v), n in excluded.most_common():
            print('   %s,%s  x%d' % (t, v, n))
    unused = [k for k in excludes if k not in excluded]
    if unused:
        print('黑名单中未命中任何上游规则（可能上游已修复，可删除）:')
        for t, v in unused:
            print('   %s,%s' % (t, v))

    unknown = [p for p in merged if p not in SLUG]
    if unknown:
        print('策略缺少 SLUG 映射，请补充后重跑: %s' % unknown)
        return 1

    if args.dry_run:
        for policy, rules in merged.items():
            print('   %-14s -> rule/%-16s %5d 条' % (policy, SLUG[policy] + '.list', len(rules)))
        return 0

    os.makedirs(OUTDIR, exist_ok=True)
    written = []
    for policy, rules in merged.items():
        path = os.path.join(OUTDIR, SLUG[policy] + '.list')
        with io.open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('# 策略: %s\n' % policy)
            fh.write('# 由 tools/sync-rules.py 自动生成，请勿手改——改动会在下次同步时丢失。\n')
            fh.write('# 需要增补规则请写进 config/default.conf 的内联规则段。\n')
            fh.write('# 上游: blackmatrix7/ios_rule_script\n')
            fh.write('# 规则数: %d\n\n' % len(rules))
            for rtype, value, mods in rules:
                fh.write(','.join((rtype, value) + mods) + chr(10))
        written.append((policy, SLUG[policy] + '.list', len(rules)))

    # 声明了策略却一条规则都没产出，说明它被前面的源全部抢走（顺序错了）。
    # 此时旧文件仍留在磁盘上会被配置继续引用，属于静默陈旧，必须报错中止。
    declared = {pol for _, pol in sets}
    empty = sorted(declared - set(merged))
    if empty:
        print('以下策略产出 0 条规则：规则全被 default.conf [Rule] 里排在更前面的'
              ' RULE-SET 拿走，或被 exclude.txt 剔除（sources.txt 的顺序只影响同一'
              '策略内的上游，不会造成这种情况）:')
        for pol in empty:
            path = os.path.join(OUTDIR, SLUG[pol] + '.list')
            stale = '  <-- 磁盘上仍有旧文件，配置会继续引用陈旧内容' \
                if os.path.exists(path) else ''
            winners = sorted({w for (w, loser) in dropped if loser == pol})
            print('   %s -> rule/%s.list%s' % (pol, SLUG[pol], stale))
            if winners:
                print('      被这些策略拿走: %s' % '、'.join(winners))
        return 1

    print('\n写出 %d 个文件:' % len(written))
    for policy, fname, n in written:
        print('   %-14s rule/%-18s %5d 条' % (policy, fname, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
