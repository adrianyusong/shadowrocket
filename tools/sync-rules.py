#!/usr/bin/env python3
"""把配置里引用的全部上游 RULE-SET 收编进本仓库。

做三件事：
  1. 按 config/default.conf 中 RULE-SET 出现的顺序拉取所有上游规则集
  2. 全局去重——同一条规则只保留首次出现的那个策略（首次即最高优先级，
     与 Shadowrocket 自上而下的匹配语义一致）。这样合并后的文件之间
     不再有跨文件冲突，顺序不再是隐式依赖
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
CONFIG = os.path.join(ROOT, 'config', 'default.conf')
OUTDIR = os.path.join(ROOT, 'rule')

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
SELF_HOSTED = 'adrianyusong/shadowrocket'


def read_rulesets():
    """按出现顺序取出 [Rule] 段里的 (url, policy)。"""
    text = io.open(CONFIG, encoding='utf-8').read()
    section = text.split('[Rule]')[1].split('[Host]')[0]
    out = []
    for line in section.split('\n'):
        line = line.strip()
        if not line.startswith('RULE-SET'):
            continue
        parts = [p.strip() for p in line.split(',')]
        url, policy = parts[1], parts[-1]
        if SELF_HOSTED in url:
            continue
        out.append((url, policy))
    return out


def fetch(url, attempts=3):
    for _ in range(attempts):
        try:
            return url, urllib.request.urlopen(url, timeout=60).read().decode('utf-8', 'ignore')
        except Exception:
            pass
    return url, None


def parse(body):
    """产出 (type, value) 序列，跳过注释与空行，并归一化语法。"""
    for line in body.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2:
            continue
        rtype = NORMALIZE.get(parts[0], parts[0])
        yield rtype, parts[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sets = read_rulesets()
    print('配置中引用的上游规则集: %d 个' % len(sets))

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
    total = 0

    for url, policy in sets:
        name = url.rsplit('/', 1)[-1].replace('.list', '')
        for rtype, value in parse(bodies[url]):
            total += 1
            key = (rtype, value)
            if key in seen:
                # 首次出现的策略优先，与自上而下匹配一致
                if seen[key] != policy:
                    dropped[(seen[key], policy)] += 1
                continue
            seen[key] = policy
            merged.setdefault(policy, []).append(key)
            stats[name] += 1

    print('原始 %d 条 -> 去重后 %d 条（丢弃 %d 条，其中跨策略冲突 %d 条）'
          % (total, len(seen), total - len(seen), sum(dropped.values())))

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
            for rtype, value in rules:
                fh.write('%s,%s\n' % (rtype, value))
        written.append((policy, SLUG[policy] + '.list', len(rules)))

    print('\n写出 %d 个文件:' % len(written))
    for policy, fname, n in written:
        print('   %-14s rule/%-18s %5d 条' % (policy, fname, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
