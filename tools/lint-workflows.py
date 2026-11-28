#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计 .github/ 下的 workflow：actionlint 查写法，zizmor 查安全问题。

check-config.py 只确认 workflow 是合法的 YAML。键名拼错、表达式引用不存在的输出、
run 脚本里的 shell 错误要靠 actionlint；把 ${{ }} 直接拼进脚本、权限过宽、令牌留在
磁盘上、action 没钉 SHA 要靠 zizmor。validate.yml 每次都跑；改 workflow 后本地也跑一遍：

    pip install -r tools/requirements-dev.txt
    python tools/lint-workflows.py

actionlint 是单文件的 Go 程序，按平台下载钉死的版本并核对 SHA-256。它和 mihomo 共用
check-mihomo.py 的缓存目录，缓存里只放压缩包：每次运行都重新核对，再解压到临时目录，
不信任缓存里现成的可执行文件。PATH 里有 shellcheck 时，actionlint 会顺带检查每个 run
脚本（GitHub 的 Ubuntu runner 自带 shellcheck）。

zizmor 从 PyPI 安装，版本钉在 tools/requirements-dev.txt。加 --offline：只看仓库里的
文件，结果不随网络或 GitHub API 限流变化。确属误报时在那一行加
`# zizmor: ignore[规则名]` 并写明理由，不要整条规则关掉。

升级 actionlint：改 ACTIONLINT_VERSION，按发布页的 actionlint_<版本>_checksums.txt
更新 ACTIONLINT_SHA256。
"""
import argparse
import importlib.util
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    'check_mihomo', os.path.join(ROOT, 'tools', 'check-mihomo.py'))
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

ACTIONLINT_VERSION = '1.7.12'
ACTIONLINT_URL = ('https://github.com/rhysd/actionlint/releases/download/v{v}/'
                  'actionlint_{v}_{plat}.{ext}')
ACTIONLINT_SHA256 = {
    'darwin_amd64': '5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644',
    'darwin_arm64': 'aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f',
    'linux_amd64': '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8',
    'linux_arm64': '325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6',
    'windows_amd64': '6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9',
}

# low 起报：zizmor 的 low 多是「令牌能少给一点」「这个 action 可以钉得更死」这类，
# 正是这里想要的。informational 只是提示，不拦。
ZIZMOR_ARGS = ['--offline', '--min-severity', 'low']


def _platform():
    return cm._platform().replace('-', '_')


def ensure_actionlint(cache, workdir):
    """核对并解压 actionlint，返回可执行文件路径（在 workdir 里）。"""
    plat = _platform()
    pin = ACTIONLINT_SHA256.get(plat)
    if not pin:
        raise cm.Failure('ACTIONLINT_SHA256 里没有 %s 平台' % plat)
    win = plat.startswith('windows')
    ext = 'zip' if win else 'tar.gz'
    asset = 'actionlint_%s_%s.%s' % (ACTIONLINT_VERSION, plat, ext)
    folder = os.path.join(cache, 'actionlint')
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, asset)
    if not (os.path.isfile(archive) and cm._sha256(archive) == pin):
        cm.say('下载 %s ……' % asset)
        cm._download(ACTIONLINT_URL.format(v=ACTIONLINT_VERSION, plat=plat, ext=ext), archive)
        got = cm._sha256(archive)
        if got != pin:
            os.remove(archive)
            raise cm.Failure('%s 的 SHA-256 是 %s，脚本里钉的是 %s——发布资产被替换或下载损坏，'
                             '拒绝运行' % (asset, got, pin))
    exe = os.path.join(workdir, 'actionlint' + ('.exe' if win else ''))
    # 只取那一个成员，按名字读出内容，不用 extractall：压缩包里的路径不可信。
    if win:
        with zipfile.ZipFile(archive) as z, z.open('actionlint.exe') as src, \
                io.open(exe, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive, 'r:gz') as t:
            src = t.extractfile(t.getmember('actionlint'))
            with src, io.open(exe, 'wb') as dst:
                shutil.copyfileobj(src, dst)
        os.chmod(exe, 0o755)
    return exe


def run_actionlint(cache):
    workdir = tempfile.mkdtemp(prefix='actionlint-')
    try:
        exe = ensure_actionlint(cache, workdir)
        shellcheck = shutil.which('shellcheck')
        cm.say('actionlint %s（SHA-256 已核对）；shellcheck：%s'
               % (ACTIONLINT_VERSION, shellcheck or '没装，跳过 run 脚本的 shell 检查'))
        rc = subprocess.call([exe, '-color' if sys.stdout.isatty() else '-no-color'], cwd=ROOT)
        cm.say('actionlint %s' % ('通过' if rc == 0 else '未通过（退出码 %d）' % rc))
        return rc == 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_zizmor():
    exe = shutil.which('zizmor')
    if not exe:
        cm.say('找不到 zizmor：pip install -r tools/requirements-dev.txt')
        return False
    args = [exe] + ZIZMOR_ARGS
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        args += ['--format', 'github']        # 结果直接标注到 PR 的 Files changed 上
    rc = subprocess.call(args + ['.'], cwd=ROOT)
    cm.say('zizmor %s' % ('通过' if rc == 0 else '未通过（退出码 %d）' % rc))
    return rc == 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description='用 actionlint 与 zizmor 审计 workflow')
    ap.parse_args(argv)
    ok = True
    try:
        ok = run_actionlint(cm.default_cache())
    except cm.Failure as exc:
        cm.say('  FAIL  %s' % exc)
        ok = False
    cm.say('')
    ok = run_zizmor() and ok
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
