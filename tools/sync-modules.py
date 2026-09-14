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


# mihomo 的协议类型来源。协议分组靠「排除其余全部类型」实现，
# ALL_TYPES 少一个，那个协议就会漏进全部四个分组——与最初
# 「VLESS 组里混着 Hy2」是同一种失效，且同样不报错。
#
# 锁定到 Clash for Apple（Hako 核心）实际用的 mihomo 版本，不是 Alpha。
# Alpha 是开发主线，新协议不断进（例如 EasyTier），拿它做基准会让每周同步
# 因为一个用户客户端还不支持的协议而反复失败。基准必须与用户设备一致。
MIHOMO_REF = 'v1.19.30'
ADAPTERS_URL = ('https://raw.githubusercontent.com/MetaCubeX/mihomo'
                '/%s/constant/adapters.go' % MIHOMO_REF)

# 路由/控制类出站，不是节点协议，不参与协议分组。
NON_PROXY_TYPES = {'Direct', 'Reject', 'RejectDrop', 'Compatible', 'Pass',
                   'PassRule', 'Rematch', 'Dns', 'Unknown'}

# 代理组类型，不是节点协议——它们也在同一个常量块里。
GROUP_TYPES = {'Relay', 'Selector', 'Fallback', 'URLTest', 'LoadBalance'}


def _warn_summary(msg):
    """告警但不失败：打到 stderr，并在 CI 里追加到步骤摘要。

    协议漂移不阻断同步，但也不能悄无声息 —— 摘要会显示在 Actions 运行页，
    本地则打到 stderr。
    """
    sys.stderr.write('[协议漂移] ' + msg + chr(10))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        try:
            with io.open(summary, 'a', encoding='utf-8') as fh:
                fh.write('⚠️ ' + msg + chr(10) + chr(10))
        except OSError:
            pass


def check_type_drift():
    """比对 build-clash.py 的 ALL_TYPES 与 mihomo 当前的节点协议清单。

    解析 constant/adapters.go 的 AdapterType 常量块，而不是 String() 方法——
    常量块按空行分成三段：控制类出站、代理组类型、节点协议。只有第三段
    参与协议分组。用 String() 会把 Selector / URLTest 这些组类型也算进来。

    Shadowrocket 2.2.92（2026-09-07）加入 Sudoku 时暴露了这个缺口：
    当时清单一口气漏了 8 个协议，每一个都会漏进全部四个协议分组。

    返回值一律为 0（本函数不阻断同步）。理由：漏一个协议是 clash.yaml 的
    前向兼容缺口，只在机场真的提供该协议时才有实际影响；而每周同步的主要
    目的是刷新广告规则，不该因为一个基准版本尚未包含的新协议就整批卡住。
    有出入时把结论打到 GITHUB_STEP_SUMMARY 与 stderr，靠邮件/摘要提醒即可。
    """
    try:
        body = fetch(ADAPTERS_URL)
    except Exception as e:                           # noqa: BLE001
        print('协议类型比对跳过（抓取失败 %s）' % e)
        return 0

    m = re.search('Direct AdapterType = iota(.*?)' + chr(10) + r'\)',
                  body, re.S)
    if not m:
        print('协议类型比对跳过：未找到 AdapterType 常量块')
        return 0
    names = [x.strip() for x in m.group(1).split(chr(10)) if x.strip()]
    upstream = set(names) - NON_PROXY_TYPES - GROUP_TYPES
    if not upstream:
        print('协议类型比对跳过：解析结果为空')
        return 0

    src = io.open(os.path.join(ROOT, 'tools', 'build-clash.py'),
                  encoding='utf-8').read()
    m2 = re.search(r'ALL_TYPES = \[(.*?)\]', src, re.S)
    if not m2:
        _warn_summary('协议类型比对失败：build-clash.py 里找不到 ALL_TYPES')
        return 0
    ours = set(re.findall(r"'([A-Za-z0-9]+)'", m2.group(1)))

    missing = sorted(upstream - ours)
    extra = sorted(ours - upstream)
    if missing:
        _warn_summary('ALL_TYPES 缺少 mihomo %s 的协议: %s —— '
                      '它们会漏进全部协议分组，补进 tools/build-clash.py 的 ALL_TYPES'
                      % (MIHOMO_REF, '、'.join(missing)))
    if extra:
        _warn_summary('ALL_TYPES 含 mihomo %s 已无的协议: %s（无害，建议清理）'
                      % (MIHOMO_REF, '、'.join(extra)))
    if not missing and not extra:
        print('协议类型比对：与 mihomo %s 一致（%d 种节点协议）'
              % (MIHOMO_REF, len(upstream)))
    return 0


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
    failed |= check_type_drift()
    return failed


if __name__ == '__main__':
    sys.exit(main())
