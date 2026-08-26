#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把上游模块收编到 module/ 下自托管。

为什么不直接订阅上游地址：
  1. 上游删库改名很频繁——本仓库核对时发现 Script-Hub、NobyDa 京东、
     RuCu6 微信去广告的地址全部 404，iRingo 整个项目也换了组织。
  2. 更重要的是，模块地址一旦失效，GitHub 用户名可被他人重新注册，
     同一个 URL 明天可以指向任何代码。自托管后这条链路由你掌握。

为什么选 AdvertisingLite 而不是 Advertising：
  后者含 11 条通配关键词规则（\b/ad/、\badvertising、\bsplash_screen 等），
  它们对每个被解密的域名生效，误伤面无法预估。前者全是具体路径规则。
"""
import io
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, 'module')

SOURCES = [
    {
        'name': 'adblock-rewrite',
        'url': 'https://raw.githubusercontent.com/blackmatrix7/ios_rule_script'
               '/master/rewrite/Shadowrocket/AdvertisingLite/AdvertisingLite.sgmodule',
        'title': '去广告重写（自托管）',
        'desc': '纯 URL 重写去广告，不含任何脚本。上游 blackmatrix7 AdvertisingLite。',
        # 低于该条数视为上游异常，拒绝写入——sync-rules.py 曾因上游返回空
        # 而让陈旧文件继续服役，这里沿用同样的下限保护。
        'min_rules': 800,
    },
]


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'curl/8'})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode('utf-8', 'replace')


def main():
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    failed = 0
    for s in SOURCES:
        try:
            body = fetch(s['url'])
        except Exception as e:                       # noqa: BLE001
            print('抓取失败 %s: %s' % (s['name'], e))
            failed = 1
            continue

        rules = [l for l in body.split(chr(10))
                 if l.strip() and not l.startswith(('#', '['))
                 and ' - reject' in l]
        if len(rules) < s['min_rules']:
            print('%s 只解析到 %d 条重写（下限 %d），疑似上游异常，保留旧文件'
                  % (s['name'], len(rules), s['min_rules']))
            failed = 1
            continue

        mitm = ''
        m = re.search(r'^hostname\s*=\s*(.+)$', body, re.M)
        if m:
            mitm = m.group(1).strip()
        hosts = [x.strip() for x in mitm.split(',') if x.strip()]

        # 丢弃上游头部，换成我们自己的；[General] 段一并保留，
        # 其 force-http-engine-hosts 是部分重写生效的前提。
        keep = body[body.index('[General]'):] if '[General]' in body \
            else body[body.index('[URL Rewrite]'):]

        out = [
            '#!name=%s' % s['title'],
            '#!desc=%s 规则 %d 条，解密域名 %d 个。' % (s['desc'], len(rules), len(hosts)),
            '#!author=adrianyusong',
            '#!homepage=https://github.com/adrianyusong/shadowrocket',
            '#!category=自托管',
            '#',
            '# 本文件由 tools/sync-modules.py 生成，不要手改——下次同步会覆盖。',
            '# 上游: %s' % s['url'],
            '#',
            '# 这个模块会解密 %d 个域名的 TLS 流量。装之前想清楚这一点：' % len(hosts),
            '# 出问题时（某个 App 登录失败、支付流程卡住），先关掉它再排查。',
            '# 作为独立模块而非写进配置，正是为了能一键关掉。',
            '',
        ]
        path = os.path.join(OUTDIR, s['name'] + '.sgmodule')
        with io.open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(chr(10).join(out) + chr(10) + keep)
        print('module/%s.sgmodule  重写 %d 条 / 解密域名 %d 个'
              % (s['name'], len(rules), len(hosts)))
    return failed


if __name__ == '__main__':
    sys.exit(main())
