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
    python tools/check-direct.py <Clash 订阅文件.yaml>
退出码：任一直连组混进中转或为空 -> 1。
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
CONFIG = os.path.join(ROOT, 'config')

# 信息类伪节点（剩余流量、到期时间），不参与核对。
INFO = re.compile(r'剩余|剩餘|到期|重置|流量|套餐|官网|官網')


def is_relay(p):
    return str(p.get('network', '')).lower() == 'ws'


def shadowrocket_groups():
    body = io.open(os.path.join(CONFIG, 'default.conf'), encoding='utf-8').read()
    out = {}
    for m in re.finditer(r'^(\S+ [^=\n]+?) = url-test, policy-regex-filter = (.*?), url =',
                         body, re.M):
        out[m.group(1).strip()] = m.group(2)
    return out


def yaml_groups(fname):
    path = os.path.join(CONFIG, fname)
    if not os.path.exists(path):
        return {}
    text = io.open(path, encoding='utf-8').read().replace('#!replace', '')
    doc = yaml.safe_load(text) or {}
    return {g['name']: g['filter'] for g in doc.get('proxy-groups') or []
            if g.get('filter')}


def check(label, groups, nodes):
    fails = 0
    direct = {n: g for n, g in groups.items() if '直连' in n}
    if not direct:
        print('%s：没有直连分组' % label)
        return 0
    print('== %s ==' % label)
    for name, rx in sorted(direct.items()):
        try:
            pat = re.compile(rx)
        except re.error as e:
            print('  FAIL %s 正则无法编译: %s' % (name, e))
            fails += 1
            continue
        region = groups.get(name.replace('直连', ''))
        rpat = re.compile(region) if region else None
        sel = [n for n, r in nodes if pat.search(n)]
        relay_in = [n for n, r in nodes if r and pat.search(n)]
        missed = [n for n, r in nodes
                  if not r and rpat and rpat.search(n) and not pat.search(n)]
        status = 'ok  '
        if relay_in or not sel:
            status = 'FAIL'
            fails += 1
        miss_txt = ('漏掉直连 %d' % len(missed)) if rpat else '无对应地区组，漏判无法统计'
        print('  %s %-10s 选中 %2d，混入中转 %d，%s'
              % (status, name, len(sel), len(relay_in), miss_txt))
        for n in relay_in:
            print('         中转: %s' % n)
        if not sel:
            print('         组为空 —— Shadowrocket 的空 url-test 组只剩 DIRECT')
        for n in missed:
            print('         漏掉: %s（安全，但少了一个可用节点）' % n)
    return fails


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    doc = yaml.safe_load(io.open(sys.argv[1], encoding='utf-8'))
    nodes = [(p['name'], is_relay(p)) for p in doc.get('proxies') or []
             if not INFO.search(p.get('name', ''))]
    print('节点 %d 个：中转 %d / 直连 %d'
          % (len(nodes), sum(r for _, r in nodes), sum(not r for _, r in nodes)))
    fails = 0
    fails += check('Shadowrocket', shadowrocket_groups(), nodes)
    fails += check('Stash', yaml_groups('stash.stoverride'), nodes)
    fails += check('Clash', yaml_groups('clash.yaml'), nodes)
    print('\n%s' % ('通过' if not fails else '未通过（%d 个分组有问题）' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
