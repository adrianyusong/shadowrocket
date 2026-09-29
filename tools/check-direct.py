#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实节点数据核对三份配置里的「直连」分组有没有混进中转节点。

为什么需要它：Shadowrocket 的 policy-regex-filter 只看得到节点名，
直连组只能靠名字里的标签猜。猜错不报错 —— #1 引入的 🇺🇲 美国直连
把 CTCU 当成直连标志，实际选中的 5 个节点全是 Cloudflare 中转，
而且它刚被设成节点选择与 AI 服务的首选。节点名本身判断不了对错，
必须拿节点的真实传输方式对照。

真实依据取自 Clash 格式的订阅文件（Clash Verge 的 profiles 目录下就有）：
    network: ws  -> 中转（前置 Cloudflare，出口是共享边缘 IP）
    其余         -> 直连（reality / tcp / hysteria2 等）
与 clash-verge/script.js 的判断口径一致。

订阅文件含服务器地址与凭据，本工具只在本地读取，不写出任何字段。
换机场或机场改了命名后重跑一次即可。

用法：
    python tools/check-direct.py <Clash 订阅文件.yaml> [更多订阅文件.yaml …]
传多份时按合并后的节点核对（配置同时装两家机场订阅时就该这样查）。
退出码 1 的情形：
  - 任一直连组混进中转
  - 任一按名筛选的组收进信息类伪节点
  - 直连组，或被某个 select 组当默认首选（policy-select-name / 第一候选）的组，
    对当前订阅筛出来是空的——空的 url-test 组在 Shadowrocket 里只剩 DIRECT，
    mihomo 里只剩 COMPATIBLE（同样是直连），默认流量会悄悄直连出去
其余被 select 组引用的空组只告警。☁️ CF优选 例外（见 EMPTY_OK）。
Clash 的协议分组按节点真实 type 计算成员（mihomo 的 exclude-type 口径）。
只读取每个节点的 name / type / network 三个字段。
"""
import io
import os
import re
import sys

try:
    import yaml
except ImportError:
    print('需要 PyYAML：pip install pyyaml')
    sys.exit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import importlib.util
_spec = importlib.util.spec_from_file_location('_bs', os.path.join(ROOT, 'tools', 'build-stash.py'))
_bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bs)
CONFIG = os.path.join(ROOT, 'config')

# 信息类伪节点（剩余流量、到期时间）。它们留在节点清单里一起核对，不预先剔除：
# 以前这里先把它们滤掉，于是「剩余流量：412 GB」被 🇬🇧 英国直连 当成英国节点
# （GB 命中了地区标签）收进组里、而且它还是 ws 中转——本工具却报「通过」。
# 与分组里的伪节点排除词同源（build-stash.py 的 PSEUDO_WORDS）。
INFO = re.compile(_bs.PSEUDO_WORDS, re.I)

# ☁️ CF优选 的节点来自另外添加的 CF 订阅，只看机场订阅时必然为空，不报。
EMPTY_OK = {'☁️ CF优选'}

# 订阅 type: 值 -> mihomo AdapterType.String()（exclude-type 比较的是后者）。
# 表里没有的按忽略大小写直接比较。
ADAPTER = {'ss': 'Shadowsocks', 'ssr': 'ShadowsocksR', 'socks5': 'Socks5',
           'http': 'Http', 'vmess': 'Vmess', 'vless': 'Vless', 'trojan': 'Trojan',
           'hysteria': 'Hysteria', 'hysteria2': 'Hysteria2', 'wireguard': 'WireGuard',
           'tuic': 'Tuic', 'ssh': 'Ssh', 'mieru': 'Mieru', 'anytls': 'AnyTLS',
           'snell': 'Snell'}


def is_relay(p):
    return str(p.get('network', '')).lower() == 'ws'


def adapter_type(p):
    t = str(p.get('type', '')).lower()
    return ADAPTER.get(t, t).lower()


def members(spec, nodes):
    """按组的筛选条件取成员。spec = (filter, exclude-filter, exclude-type 集合)。"""
    inc, exc, types = spec
    inc = re.compile(inc) if inc else None
    exc = re.compile(exc) if exc else None
    return [x for x in nodes
            if (inc is None or inc.search(x[0]))
            and not (exc and exc.search(x[0]))
            and x[3] not in types]


def shadowrocket_groups():
    """返回 (按名筛选的组 {名字: spec}, select 组 {名字: 候选}, 默认首选的组集合)。"""
    body = io.open(os.path.join(CONFIG, 'default.conf'), encoding='utf-8').read()
    filtered, selects, defaults, inside = {}, {}, set(), False
    for line in body.splitlines():
        t = line.strip()
        if t.startswith('[') and t.endswith(']'):
            inside = t == '[Proxy Group]'
            continue
        if not inside or not t or t.startswith('#') or '=' not in t:
            continue
        name, rest = [x.strip() for x in t.split('=', 1)]
        if 'policy-regex-filter = ' in rest:
            rx = rest.split('policy-regex-filter = ', 1)[1]
            filtered[name] = (rx.split(', url =', 1)[0], None, set())
        else:
            parts = [x.strip() for x in rest.split(',')]
            if parts[0] == 'select':
                cands = [x for x in parts[1:] if x and '=' not in x]
                selects[name] = cands
                psn = [x.split('=', 1)[1].strip() for x in parts[1:]
                       if x.startswith('policy-select-name')]
                if psn or cands:
                    defaults.add(psn[0] if psn else cands[0])
    return filtered, selects, defaults


def yaml_groups(fname):
    path = os.path.join(CONFIG, fname)
    if not os.path.exists(path):
        return {}, {}
    doc = yaml.safe_load(io.open(path, encoding='utf-8').read().replace('#!replace', '')) or {}
    groups = doc.get('proxy-groups') or []
    filtered = {g['name']: (g.get('filter'), g.get('exclude-filter'),
                            {t.lower() for t in str(g.get('exclude-type') or '').split('|') if t})
                for g in groups
                if g.get('filter') or (g.get('include-all') and g.get('exclude-type'))}
    selects = {g['name']: g.get('proxies') or [] for g in groups
               if g.get('type') == 'select' and g.get('proxies')}
    # Stash / Clash 没有 policy-select-name，select 组默认选第一个候选
    defaults = {v[0] for v in selects.values() if v}
    return filtered, selects, defaults


def check(label, groups, nodes):
    filtered, selects, defaults = groups
    fails = 0
    if not filtered:
        print('%s：没有按节点名筛选的分组' % label)
        return 0
    print('== %s ==' % label)
    referenced = {c for cands in selects.values() for c in cands}
    for name, spec in sorted(filtered.items()):
        try:
            sel = members(spec, nodes)
        except re.error as e:
            print('  FAIL %s 正则无法编译: %s' % (name, e))
            fails += 1
            continue
        pseudo = [x[0] for x in sel if x[2]]
        real = [(x[0], x[1]) for x in sel if not x[2]]
        relay_in = [n for n, r in real if r] if '直连' in name else []
        empty = not real and name not in EMPTY_OK
        critical = '直连' in name or name in defaults
        if pseudo or relay_in or (empty and critical):
            status = 'FAIL'
            fails += 1
        elif empty and name in referenced:
            status = 'WARN'
        else:
            status = 'ok  '
        if status == 'ok  ' and '直连' not in name:
            continue                      # 只把直连组与有问题的组打出来，避免刷屏
        extra = ''
        if '直连' in name:
            region = filtered.get(name.replace('直连', ''))
            if region:
                inside = {x[0] for x in sel}
                missed = [x[0] for x in members(region, nodes)
                          if not x[1] and not x[2] and x[0] not in inside]
                extra = '，漏掉直连 %d' % len(missed)
        print('  %s %-10s 选中 %2d，混入中转 %d，伪节点 %d%s'
              % (status, name, len(real), len(relay_in), len(pseudo), extra))
        for n in relay_in:
            print('         中转: %s' % n)
        for n in pseudo:
            print('         伪节点: %s' % n)
        if empty and status != 'ok  ':
            print('         空组%s——Shadowrocket 的空 url-test 组只剩 DIRECT，'
                  'mihomo 里只剩 COMPATIBLE（同样直连）'
                  % ('，且是某个 select 组的默认首选' if name in defaults
                     else '，被 select 组引用'))
    return fails


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    proxies = []
    for path in sys.argv[1:]:
        doc = yaml.safe_load(io.open(path, encoding='utf-8')) or {}
        proxies += doc.get('proxies') or []
    # (名字, 是否中转, 是否伪节点, AdapterType 小写)。不读其余字段。
    nodes = [(p['name'], is_relay(p), bool(INFO.search(p.get('name', ''))), adapter_type(p))
             for p in proxies]
    real = [x for x in nodes if not x[2]]
    print('节点 %d 个：中转 %d / 直连 %d / 信息类伪节点 %d'
          % (len(nodes), sum(x[1] for x in real), sum(not x[1] for x in real),
             len(nodes) - len(real)))
    fails = 0
    fails += check('Shadowrocket', shadowrocket_groups(), nodes)
    fails += check('Stash', yaml_groups('stash.stoverride'), nodes)
    fails += check('Clash', yaml_groups('clash.yaml'), nodes)
    print('\n%s' % ('通过' if not fails else '未通过（%d 个分组有问题）' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
