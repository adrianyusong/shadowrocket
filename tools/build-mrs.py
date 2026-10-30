#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 stash/ 的 domain / ipcidr 规则集转成 MRS，给 config/clash.yaml 用。

为什么要转：clash.md 的配置最佳实践写明，iOS 上 Clash Core 跑在 Network Extension 里，
可用内存约 50 MiB（社区反馈与官方真机测试的估计，不是 Apple 公布的数字）。大型
format: text 规则集要在设备上解析并构建匹配结构，叠几份就可能把扩展挤爆——表现是
配置启动失败、连接突然断开、代理进程被系统结束。MRS 是 mihomo 的预编译二进制格式，
省掉了解析和临时内存。我们光 reject-ads 一份就有 8 万多条域名。

只转 domain 与 ipcidr：MRS 不支持 classical，那几份照旧用 stash/*.txt。
Stash 不认 MRS，stash/*.txt 仍是 Stash 的规则集，也是 MRS 唯一的来源。

转换用 check-mihomo.py 钉死版本、核对过 SHA-256 的同一个内核（convert-ruleset 子命令），
不另装工具。产物逐字节确定：同一份 .txt、同一个内核版本，转出来完全一致，所以
validate.yml 能靠「重新生成后工作区无变化」核对 mrs/ 没有过期。升级内核后 MRS 可能
整体变化，属正常，随升级一起提交即可。

用法：
    python tools/build-mrs.py          （先跑 build-stash.py，再跑 build-clash.py）
"""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRCDIR = os.path.join(ROOT, 'stash')
OUTDIR = os.path.join(ROOT, 'mrs')
KINDS = ('domain', 'ipcidr')

# 内核下载、SHA-256 核对、缓存目录都只有 check-mihomo.py 一份实现。
_spec = importlib.util.spec_from_file_location(
    'check_mihomo', os.path.join(ROOT, 'tools', 'check-mihomo.py'))
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def sources():
    """stash/ 里能转 MRS 的规则集：(文件名主干, behavior)。"""
    out = []
    for f in sorted(os.listdir(SRCDIR)):
        stem, ext = os.path.splitext(f)
        kind = stem.rsplit('-', 1)[-1]
        if ext == '.txt' and kind in KINDS:
            out.append((stem, kind))
    return out


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    if not os.path.isdir(SRCDIR):
        print('找不到 stash/，请先运行 tools/build-stash.py')
        return 1
    todo = sources()
    if not todo:
        print('stash/ 里没有 domain / ipcidr 规则集，请先运行 tools/build-stash.py')
        return 1
    try:
        exe, key = cm.ensure_binary(cm.default_cache())
    except cm.Failure as exc:
        print('取不到内核：%s' % exc)
        return 1
    os.makedirs(OUTDIR, exist_ok=True)

    # 与 build-stash.py 一样先清旧产物：删掉某个策略后，残留的 .mrs 不能还挂在仓库里。
    for f in os.listdir(OUTDIR):
        if f.endswith('.mrs'):
            os.remove(os.path.join(OUTDIR, f))

    total = 0
    for stem, kind in todo:
        src = os.path.join(SRCDIR, stem + '.txt')
        dst = os.path.join(OUTDIR, stem + '.mrs')
        p = subprocess.run([exe, 'convert-ruleset', kind, 'text', src, dst],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode != 0 or not os.path.isfile(dst) or not os.path.getsize(dst):
            print('转换失败 stash/%s.txt：%s' % (
                stem, p.stdout.decode('utf-8', 'replace').strip()[-500:] or '没有产物'))
            return 1
        total += os.path.getsize(dst)

    print('mrs/  %d 个规则集 / %.1f KB（mihomo %s 转换，%s）'
          % (len(todo), total / 1024.0, cm.MIHOMO_REF, key))
    return 0


if __name__ == '__main__':
    sys.exit(main())
