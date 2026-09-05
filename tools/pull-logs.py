#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Windows 持续拉取 iOS Shadowrocket 的连接日志。

Shadowrocket 没有任何网络 API、远程控制器或 syslog 转发，日志只是它
Documents 目录下的 SQLite 文件（proxy-YYYY-MM-DD-HHMMSS.db）。唯一的
持续获取方式是通过 USB 的 AFC 协议轮询那个目录 —— AFC 的完整操作码里
没有 watch / subscribe / notify，所以只能轮询，这是协议限制不是工具缺陷。

前置条件（缺一不可）：
  1. pip install -U pymobiledevice3
  2. 装 iTunes 或 Microsoft Store 的「Apple 设备」——它提供
     AppleMobileDeviceService，pymobiledevice3 靠它在 127.0.0.1:27015
     跟设备通信。没有它，USB 这条路完全走不通。
  3. 手机用数据线连上，信任本机，且开机后至少解锁过一次。
  4. Shadowrocket 里开日志：数据 → 代理 → 启用日志记录。

WAL 是这里最容易出错的地方：Shadowrocket 边跑边写，新记录在 -wal 里而不在
主库。只拷 .db 会得到空库或过期快照 —— 本仓库 Downloads 里那份
proxy-2026-08-18-155007.db 就是这样，4096 字节、0 张表。所以三个文件
必须一起拉，拉完还要 PRAGMA quick_check 验一遍。

用法：
  python tools/pull-logs.py --out D:\\shadowrocket\\logs
  python tools/pull-logs.py --out ... --interval 5 --once
"""
import argparse
import asyncio
import os
import shutil
import sqlite3
import sys

BUNDLE = 'com.liguangming.Shadowrocket'
SUFFIXES = ('.db', '.db-wal', '.db-shm')


def check_copy(path):
    """验证拉下来的副本是否完整，并把 WAL 合进主库。

    以读写方式打开才会触发 WAL 恢复；只读打开拿不到 -wal 里的新记录。
    quick_check 不为 ok 说明这次拉取撞上了 checkpoint，丢掉重来即可 ——
    AFC 上不存在一致性快照，偶尔撕裂是正常的，不是 bug。
    """
    try:
        con = sqlite3.connect(path)
        ok = con.execute('PRAGMA quick_check').fetchone()[0]
        if ok != 'ok':
            con.close()
            return False, ok
        con.execute('PRAGMA journal_mode=DELETE')   # 合并 WAL，副本自包含
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        rows = 0
        for t in tables:
            try:
                rows += con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            except sqlite3.Error:
                pass
        con.close()
        if not tables:
            # 结构合法但没有任何表 —— 这正是手动导出最常见的失败方式：
            # 日志没开、或本次会话还没写过任何东西。不是拉取出错，
            # 但也不该当成成功一带而过。
            return True, '空库（0 表）—— 检查 数据 → 代理 → 启用日志记录 是否已开'
        return True, '%d 表 / %d 行' % (len(tables), rows)
    except sqlite3.Error as e:
        return False, str(e)


async def pull_once(ha, outdir, seen, verbose=True):
    names = [n for n in await ha.listdir('/Documents') if n.endswith(SUFFIXES)]
    stems = sorted({n.split('.db')[0] for n in names})
    pulled = 0
    for stem in stems:
        group = [n for n in names if n.startswith(stem + '.db')]
        # 用整组的 (大小, 修改时间) 做变更检测。AFC 没有变更通知，
        # 但 stat 很便宜，避免了没变化时白拉整个文件。
        sig = []
        for n in sorted(group):
            try:
                st = await ha.stat('/Documents/' + n)
                sig.append((n, st.get('st_size'), str(st.get('st_mtime'))))
            except Exception:
                pass
        sig = tuple(sig)
        if seen.get(stem) == sig:
            continue

        # 三个文件连续拉，中间不做别的，尽量缩短撕裂窗口。
        tmp = os.path.join(outdir, '.tmp')
        os.makedirs(tmp, exist_ok=True)
        for n in sorted(group):
            await ha.pull('/Documents/' + n, os.path.join(tmp, n),
                          progress_bar=False)

        dbfile = os.path.join(tmp, stem + '.db')
        if not os.path.exists(dbfile):
            continue
        ok, info = check_copy(dbfile)
        if not ok:
            if verbose:
                print('  %s 校验失败(%s)，丢弃重来' % (stem, info))
            continue

        for n in sorted(group):
            src = os.path.join(tmp, n)
            if os.path.exists(src):
                shutil.move(src, os.path.join(outdir, n))
        seen[stem] = sig
        pulled += 1
        if verbose:
            print('  %s  %s' % (stem, info))
    return pulled


async def run(args):
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.house_arrest import HouseArrestService
    except ImportError:
        print('缺少依赖，先执行：pip install -U pymobiledevice3')
        return 1

    os.makedirs(args.out, exist_ok=True)
    seen = {}
    while True:
        try:
            # 11.x 的 API 是异步的；网上 <=9.x 的同步示例在这里跑不通。
            lockdown = await create_using_usbmux()
            # documents_only 必须为真：Shadowrocket 是 App Store 应用，
            # VendContainer 会被拒绝，且 pymobiledevice3 不会自动回退。
            async with await HouseArrestService.create(
                    lockdown, BUNDLE, documents_only=True) as ha:
                print('已连接，轮询间隔 %ds，输出到 %s' % (args.interval, args.out))
                while True:
                    n = await pull_once(ha, args.out, seen)
                    if args.once:
                        return 0
                    if n == 0:
                        print('.', end='', flush=True)
                    await asyncio.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
        except Exception as e:                      # noqa: BLE001
            # 拔线、息屏、App 被杀、重启都会断开长连接，重连不是可选项。
            print('\n连接中断(%s)，5 秒后重连' % e)
            if args.once:
                return 1
            await asyncio.sleep(5)


def main():
    ap = argparse.ArgumentParser(description='持续拉取 iOS Shadowrocket 连接日志')
    ap.add_argument('--out', required=True, help='本地输出目录')
    ap.add_argument('--interval', type=int, default=5,
                    help='轮询秒数，默认 5。USB 上 2-5 秒手感接近实时，'
                         '低于 0.5 秒只是白白唤醒设备')
    ap.add_argument('--once', action='store_true', help='拉一次就退出')
    args = ap.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
