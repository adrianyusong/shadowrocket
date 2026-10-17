#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实的 mihomo 内核载入 Clash 配置与规则集，补上 check-config.py 看不到的那一层。

check-config.py 用 Python 的 re / PyYAML 去「推测」mihomo 会怎么理解这些文件。
推测与内核之间的差距，已经实测到三类：
  1. 正则引擎不同。group filter 走 regexp2，Python 合法的 (?P<n>…) 它直接 panic。
  2. 配置项被移除却不报错。global-client-fingerprint 在 v1.19 已删，内核只记一行
     level=error，退出码仍是 0——只看退出码会漏掉。
  3. 规则集静默少载。mihomo -t 根本不加载规则集；完整启动后才看得到 USER-AGENT 这类
     内核不认的规则被丢弃，以及 ASN 库下载失败时含 IP-ASN 的规则集整包少规则。
     这正是 README 里「规则静默消失，不报错」的那一类故障。

做法：
  * 内核版本钉死（与 sync-modules.py 的 MIHOMO_REF 同一处），下载后核对 SHA-256，
    不符就拒绝运行。
  * 订阅用 tools/fixtures/mihomo-nodes.yaml 的假节点代替。
  * 规则集取本地 stash/*.txt（测的是这次提交的内容，不是 main 上的），地理库预先备好，
    测试不依赖 CDN。
  * 完整启动 config/clash.yaml，经 REST API 核对：每个规则集「载入条数 + 内核不认的
    USER-AGENT 条数」必须等于文件行数；策略组与规则条数与配置一致；日志里除已知的
    USER-AGENT 告警外，不许出现任何 warning / error。
  * clash-verge/config.yaml 的规则集在外部站点，这里只做 -t（解析）这一层。

升级内核：改 sync-modules.py 的 MIHOMO_REF，再用 --print-pins 取新版本各平台的
官方 SHA-256 更新下面的 PINS。

用法：
    python tools/check-mihomo.py
    python tools/check-mihomo.py --print-pins
    python tools/check-mihomo.py --config X.yaml --stash-dir DIR    # 只完整启动指定配置
"""
import argparse
import collections
import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

try:
    import yaml
except ImportError:
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 sync-modules.py 的协议漂移检查共用同一个版本号，只有一处定义。
_spec = importlib.util.spec_from_file_location(
    'sync_modules', os.path.join(ROOT, 'tools', 'sync-modules.py'))
_sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sm)
MIHOMO_REF = _sm.MIHOMO_REF

PLATFORMS = ('darwin-amd64', 'darwin-arm64', 'linux-amd64', 'linux-arm64', 'windows-amd64')

# 官方发布页为每个资产列出的 SHA-256。
PINS = {
    'v1.19.30': {
        'darwin-amd64': '99dfcfe454ed58fb95ee4ba222c39defd051b687ad3e5deabb1b9d6be3103e2f',
        'darwin-arm64': '2c7f3a7904fa1cee291e124123e630e7b1ebd13765dd9bf26c0a28432004d9f4',
        'linux-amd64': 'cf06ce2c7d1421bdbda14ee4a5b6046672dc35ebf8eecd8e77504ec3c0ed9a84',
        'linux-arm64': '58896873736d28628f66de3677c8654fa0f180662523148e136cff4f6e890069',
        'windows-amd64': '22c09fd67673895ef7cd6b1820563918275c3d316f2462b306208675118db3c0',
    },
}

# 工作目录里的文件名 -> meta-rules-dat 发布页的资产名。文件名是内核自己的叫法
# （geoip.metadb 里放的是 country.mmdb 的内容），名字对不上时内核会改走下载，不会出错，
# 只是慢，且重新依赖网络。
GEODATA_BASE = 'https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/'
GEODATA = {
    'GeoSite.dat': 'geosite.dat',
    'GeoIP.dat': 'geoip.dat',
    'geoip.metadb': 'country.mmdb',
    'ASN.mmdb': 'GeoLite2-ASN.mmdb',
}
GEOX_ASSET = {'geoip': 'geoip.dat', 'geosite': 'geosite.dat',
              'mmdb': 'country.mmdb', 'asn': 'GeoLite2-ASN.mmdb'}
GEODATA_TTL = 7 * 86400

DEFAULT_LIVE = os.path.join(ROOT, 'config', 'clash.yaml')
DEFAULT_PARSE_ONLY = [os.path.join(ROOT, 'clash-verge', 'config.yaml')]
STASH_DIR = os.path.join(ROOT, 'stash')
FIXTURE = os.path.join(ROOT, 'tools', 'fixtures', 'mihomo-nodes.yaml')

# 内核认不得 USER-AGENT 规则（Stash 认，所以它们留在共用的 classical 规则集里）。
# 这是已知取舍：下面逐条核对条数，其余任何丢失都算失败。
ALLOWED_LOG = [
    re.compile(r'^parse classical rule \[USER-AGENT,.*\] error: '
               r'unsupported rule type: USER-AGENT$'),
]
LOG_LINE = re.compile(r'level=(warn(?:ing)?|error|fatal)\s+msg="(.*)"\s*$')

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 控制器在本机，不走代理


class Failure(Exception):
    """环境层面的失败（下载 / 校验 / 脚手架与配置对不上），直接中止。"""


def say(msg=''):
    print(msg, flush=True)


def _rel(path):
    path = os.path.abspath(path)
    return os.path.relpath(path, ROOT).replace(os.sep, '/') if path.startswith(ROOT) else path


def _sha256(path):
    h = hashlib.sha256()
    with io.open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'check-mihomo/1'})
            with urllib.request.urlopen(req, timeout=120) as r, \
                    io.open(dest + '.part', 'wb') as out:
                shutil.copyfileobj(r, out)
            os.replace(dest + '.part', dest)
            return
        except (OSError, ValueError, http.client.HTTPException) as exc:
            last = exc
            time.sleep(3 * (i + 1))
    raise Failure('下载失败 %s：%s' % (url, last))


def _platform():
    system = {'Windows': 'windows', 'Linux': 'linux', 'Darwin': 'darwin'}.get(platform.system())
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'arm64': 'arm64',
            'aarch64': 'arm64'}.get(platform.machine().lower())
    if not system or not arch:
        raise Failure('不支持的平台 %s / %s' % (platform.system(), platform.machine()))
    return '%s-%s' % (system, arch)


def ensure_binary(cache):
    key = _platform()
    pin = PINS.get(MIHOMO_REF, {}).get(key)
    if not pin:
        raise Failure('tools/check-mihomo.py 里没有 %s 在 %s 上的 SHA-256。升级 MIHOMO_REF 后'
                      '必须同步更新 PINS：python tools/check-mihomo.py --print-pins'
                      % (MIHOMO_REF, key))
    win = key.startswith('windows')
    asset = 'mihomo-%s-%s.%s' % (key, MIHOMO_REF, 'zip' if win else 'gz')
    folder = os.path.join(cache, MIHOMO_REF)
    os.makedirs(folder, exist_ok=True)
    exe = os.path.join(folder, 'mihomo-%s%s' % (key, '.exe' if win else ''))
    stamp = exe + '.sha256'
    if os.path.isfile(exe) and os.path.isfile(stamp) \
            and io.open(stamp, encoding='utf-8').read().strip() == pin:
        return exe, key

    say('下载 %s ……' % asset)
    archive = os.path.join(folder, asset)
    _download('https://github.com/MetaCubeX/mihomo/releases/download/%s/%s'
              % (MIHOMO_REF, asset), archive)
    got = _sha256(archive)
    if got != pin:
        os.remove(archive)
        raise Failure('%s 的 SHA-256 是 %s，脚本里钉的是 %s——发布资产被替换或下载损坏，拒绝运行'
                      % (asset, got, pin))
    if win:
        with zipfile.ZipFile(archive) as z:
            names = [n for n in z.namelist() if n.lower().endswith('.exe')]
            if len(names) != 1:
                raise Failure('%s 里应当只有一个 .exe，实际 %s' % (asset, names))
            with z.open(names[0]) as src, io.open(exe, 'wb') as dst:
                shutil.copyfileobj(src, dst)
    else:
        with gzip.open(archive, 'rb') as src, io.open(exe, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        os.chmod(exe, 0o755)
    os.remove(archive)
    with io.open(stamp, 'w', encoding='utf-8') as fh:
        fh.write(pin)
    return exe, key


def ensure_geodata(cache, names):
    folder = os.path.join(cache, 'geodata')
    os.makedirs(folder, exist_ok=True)
    got = {}
    for name in names:
        asset = GEODATA[name]
        path = os.path.join(folder, asset)
        if not (os.path.isfile(path) and time.time() - os.path.getmtime(path) < GEODATA_TTL):
            say('下载 %s ……' % asset)
            _download(GEODATA_BASE + asset, path)
        got[name] = path
    return got


def geodata_needed(doc, live):
    names = ['GeoSite.dat', 'GeoIP.dat' if doc.get('geodata-mode') else 'geoip.metadb']
    if live:
        names.append('ASN.mmdb')
    return names


def patch_config(text, ports=None, secret=None):
    """只改测试必须改的几行，其余一字不动。返回 (新文本, 各项改写次数)。

    geox-url 换成 GitHub 发布页：地理库本来就预先备好了，这里只是让「名字对不上
    而改走下载」时也落在可靠的源上。ports / secret 给了才改监听：避免占用本机
    已有 Clash 的端口，也避免 0.0.0.0 上开 DNS。
    """
    out, n, section = [], collections.Counter(), None
    for ln in text.split('\n'):
        top = re.match(r'([A-Za-z0-9_-]+):', ln)
        if top:
            section = top.group(1)
        if ports:
            if re.match(r'mixed-port:', ln):
                ln, n['mixed-port'] = 'mixed-port: %d' % ports[0], n['mixed-port'] + 1
            elif re.match(r'external-controller:', ln):
                ln = 'external-controller: 127.0.0.1:%d' % ports[1]
                n['external-controller'] += 1
            elif re.match(r'secret:', ln):
                ln, n['secret'] = 'secret: "%s"' % secret, n['secret'] + 1
            elif section == 'dns' and re.match(r'  listen:', ln):
                ln, n['dns.listen'] = '  listen: 127.0.0.1:%d' % ports[2], n['dns.listen'] + 1
        if section == 'geox-url':
            g = re.match(r'  (geoip|geosite|mmdb|asn):', ln)
            if g:
                ln = '  %s: %s%s' % (g.group(1), GEODATA_BASE, GEOX_ASSET[g.group(1)])
                n['geox-url'] += 1
        out.append(ln)
    return '\n'.join(out), n


def _free_ports(count):
    socks = []
    try:
        for _ in range(count):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            socks.append(s)
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


def _count_rules(path):
    """(规则行数, 其中 USER-AGENT 条数)。"""
    lines = [ln.strip() for ln in io.open(path, encoding='utf-8')]
    lines = [ln for ln in lines if ln and not ln.startswith('#')]
    return len(lines), sum(1 for ln in lines if ln.startswith('USER-AGENT,'))


def _log_problems(text):
    """日志里不在白名单内的 warning / error / fatal。同类（只差方括号里的规则）合并计数。"""
    counts = {}
    for ln in text.splitlines():
        m = LOG_LINE.search(ln)
        if not m or any(rx.match(m.group(2)) for rx in ALLOWED_LOG):
            continue
        key = '%s: %s' % (m.group(1), re.sub(r'\[[^\]]*\]', '[…]', m.group(2)))
        counts[key] = counts.get(key, 0) + 1
    return ['%s%s' % (k[:300], ' (×%d)' % n if n > 1 else '') for k, n in counts.items()]


def _run_parse(exe, home, cfg, timeout=180):
    try:
        p = subprocess.run([exe, '-t', '-d', home, '-f', cfg], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, ''
    return p.returncode, p.stdout.decode('utf-8', 'replace')


def _api(port, secret, path):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                 headers={'Authorization': 'Bearer ' + secret})
    with _OPENER.open(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


def _compare(api_providers, expected):
    probs, total = [], 0
    for name, (lines, ua) in sorted(expected.items()):
        got = api_providers.get(name)
        if got is None:
            probs.append('规则集 %s 没有出现在内核里' % name)
            continue
        n = got.get('ruleCount') or 0
        total += n
        if n + ua != lines:
            probs.append('规则集 %s 载入 %d 条，文件有 %d 行（其中 USER-AGENT %d 条内核不认），'
                         '少了 %d 条' % (name, n, lines, ua, lines - ua - n))
    return probs, total


def _stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)


def _live(exe, home, cfg, doc, ports, secret, expected, timeout):
    """完整启动并经 REST API 核对。返回 (问题列表, 统计)。"""
    ctl = ports[1]
    problems, stats = [], {}
    text = ''
    logpath = os.path.join(home, 'core.log')
    with io.open(logpath, 'wb') as logf:
        proc = subprocess.Popen([exe, '-d', home, '-f', cfg], stdout=logf,
                                stderr=subprocess.STDOUT)
        try:
            deadline, last = time.time() + timeout, ['内核一直没有就绪']
            ready = False
            while not ready:
                if proc.poll() is not None:
                    problems.append('内核启动后退出了（退出码 %s）' % proc.returncode)
                    break
                try:
                    provs = _api(ctl, secret, '/providers/rules')['providers']
                    probs, total = _compare(provs, expected)
                    last = probs or last
                    if not probs:
                        stats['loaded'] = total
                        ready = True
                        time.sleep(2)               # 留点余量，让随后的日志落盘
                        continue
                except (OSError, ValueError, KeyError, http.client.HTTPException):
                    pass
                if time.time() > deadline:
                    problems.extend(last[:20])
                    if len(last) > 20:
                        problems.append('……另有 %d 条同类问题' % (len(last) - 20))
                    break
                time.sleep(1)

            if ready:
                groups = [g['name'] for g in doc.get('proxy-groups') or []]
                known = _api(ctl, secret, '/proxies')['proxies']
                missing = [g for g in groups if g not in known]
                if missing:
                    problems.append('策略组没有被内核创建: %s' % '、'.join(missing[:10]))
                rules = _api(ctl, secret, '/rules')['rules']
                if len(rules) != len(doc.get('rules') or []):
                    problems.append('内核加载了 %d 条规则，配置里有 %d 条'
                                    % (len(rules), len(doc.get('rules') or [])))
                stats.update(groups=len(groups), rules=len(rules))
        finally:
            # 先取日志再发停止信号：Linux 上内核收到 SIGTERM 会自己记一行
            # "Mihomo shutting down" 的 warning，那不是配置问题。
            text = io.open(logpath, encoding='utf-8', errors='replace').read()
            _stop(proc)
    problems.extend('内核日志 %s' % p for p in _log_problems(text))
    return problems, stats


def run_target(exe, cache, path, live, stash_dir, timeout):
    """校验一份配置。返回 (问题列表, 说明行列表)。"""
    label = _rel(path)
    text = io.open(path, encoding='utf-8').read().replace('\r\n', '\n')
    doc = yaml.safe_load(text)
    ports = _free_ports(3) if live else None
    secret = 'check-mihomo-%d' % ports[0] if live else None

    patched, counts = patch_config(text, ports, secret)
    need = {}
    if re.search(r'(?m)^geox-url:', text):
        need['geox-url'] = 4
    if live:
        need.update({'external-controller': 1, 'secret': 1})
    for key, want in need.items():
        if counts[key] != want:
            raise Failure('%s：测试脚手架没能改写 %s（应为 %d 处，实际 %d 处）——配置格式变了，'
                          '请同步更新 tools/check-mihomo.py 的 patch_config'
                          % (label, key, want, counts[key]))
    for key in ('mixed-port', 'dns.listen'):
        if counts[key] > 1:
            raise Failure('%s：%s 出现了 %d 处，patch_config 只认一处' % (label, key, counts[key]))

    geo = ensure_geodata(cache, geodata_needed(doc, live))
    problems, notes = [], []
    home = tempfile.mkdtemp(prefix='check-mihomo-')
    try:
        for name, src in geo.items():
            shutil.copyfile(src, os.path.join(home, name))
        for name, prov in (doc.get('proxy-providers') or {}).items():
            rel = prov.get('path')
            if not rel:
                raise Failure('%s：proxy-provider %s 没有 path，无法用假节点代替' % (label, name))
            dst = os.path.join(home, os.path.normpath(rel))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(FIXTURE, dst)

        expected = {}
        if live:
            for name, prov in (doc.get('rule-providers') or {}).items():
                rel = os.path.normpath(prov.get('path') or '')
                src = os.path.join(stash_dir, os.path.basename(rel))
                if os.path.isabs(rel) or rel.startswith('..') or not os.path.isfile(src):
                    problems.append('规则集 %s 对应的文件不存在：%s' % (name, _rel(src)))
                    continue
                dst = os.path.join(home, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                expected[name] = _count_rules(src)

        cfg = os.path.join(home, 'config.yaml')
        with io.open(cfg, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(patched)

        rc, out = _run_parse(exe, home, cfg)
        parse_bad = _log_problems(out)
        if rc is None:
            problems.append('-t 超时')
        elif rc != 0:
            lines = [ln for ln in out.splitlines() if ln.strip()]
            key = [ln for ln in lines if re.match(r'panic:|.*level=(error|fatal)', ln)]
            problems.append('-t 退出码 %s：%s' % (rc, ' / '.join(t[:300] for t in (key[:2] or lines[-4:]))))
        problems.extend('-t 日志 %s' % p for p in parse_bad)
        if rc == 0 and not parse_bad:
            notes.append('-t 解析通过')

        if live and not problems:
            live_problems, stats = _live(exe, home, cfg, doc, ports, secret, expected, timeout)
            problems.extend(live_problems)
            if not live_problems:
                ua = sum(u for _, u in expected.values())
                notes.append('完整启动：%d 个规则集载入 %d 条；策略组 %d 个，规则 %d 条'
                             % (len(expected), stats['loaded'], stats['groups'], stats['rules']))
                if ua:
                    notes.append('已知取舍：%d 条 USER-AGENT 规则 Stash 认、mihomo 不认，'
                                 'Clash 侧不生效' % ua)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return problems, notes


def print_pins():
    req = urllib.request.Request(
        'https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/%s' % MIHOMO_REF,
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'check-mihomo/1'})
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode('utf-8'))
    assets = {a['name']: a.get('digest') or '' for a in data['assets']}
    say("    '%s': {" % MIHOMO_REF)
    for key in PLATFORMS:
        asset = 'mihomo-%s-%s.%s' % (key, MIHOMO_REF, 'zip' if key.startswith('windows') else 'gz')
        say("        '%s': '%s'," % (key, assets.get(asset, '').replace('sha256:', '') or '（缺）'))
    say('    },')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description='用真实 mihomo 内核载入 Clash 配置与规则集')
    ap.add_argument('--config', help='只对这份配置做完整启动（测试用）')
    ap.add_argument('--stash-dir', default=STASH_DIR)
    ap.add_argument('--timeout', type=int, default=120, help='等待规则集载入的秒数')
    ap.add_argument('--print-pins', action='store_true', help='打印 MIHOMO_REF 各平台的官方 SHA-256')
    args = ap.parse_args(argv)

    if yaml is None:
        say('需要 PyYAML：pip install pyyaml')
        return 1

    if args.print_pins:
        print_pins()
        return 0

    cache = os.environ.get('CHECK_MIHOMO_CACHE') or os.path.join(
        tempfile.gettempdir(), 'shadowrocket-check-mihomo')
    targets = [(os.path.abspath(args.config), True)] if args.config else \
        [(DEFAULT_LIVE, True)] + [(p, False) for p in DEFAULT_PARSE_ONLY]
    failed = 0
    try:
        exe, key = ensure_binary(cache)
        say('mihomo %s（%s），SHA-256 已核对' % (MIHOMO_REF, key))
        for path, live in targets:
            problems, notes = run_target(exe, cache, path, live, args.stash_dir, args.timeout)
            say('')
            say(_rel(path))
            for n in notes:
                say('  ok    %s' % n)
            for p in problems:
                say('  FAIL  %s' % p)
            failed += len(problems)
    except Failure as exc:
        say('  FAIL  %s' % exc)
        failed += 1
    say('')
    say('%s（失败 %d）' % ('通过' if not failed else '未通过', failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
