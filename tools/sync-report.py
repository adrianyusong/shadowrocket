#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步 PR 的摘要，以及需要人工审核的变更。

同步 PR 在 validate 通过后自动合并，没人看 diff——每周几千到上万行，也看不过来。
validate 管的是「对不对」（语法、引用、内核能否载入），管不了「该不该」：上游往模块里
多加一个解密域名、往 direct 里塞一条 0.0.0.0/1，校验全都通过。这里把 diff 压成两部分：

  1. 各文件条数的增减与增删样例，写进 PR 描述与运行摘要，想看时一眼能看完。
  2. 高风险项。命中任何一条，sync.yml 就不开自动合并，改贴 needs-review 标签并开 issue：
     * 模块新增 MITM 主机或 force-http-engine-hosts——解密范围扩大。
     * 规则集新增宽于 /8 的 IPv4 或宽于 /16 的 IPv6 网段——一条就能让大片流量改道。
     * 规则集新增裸顶级域后缀（DOMAIN-SUFFIX,xyz、DOMAIN-WILDCARD,*.xyz）——一条吞掉整个顶级域。
     * 规则集新增 IP-ASN / GEOIP / GEOSITE 这类整片规则。
   只看「新增」：仓库里已有的 224.0.0.0/3、DOMAIN-SUFFIX,cn 早已审过，不重复报。
   跳转、改写这类模块写法不在这里：check-config.py 的 check_modules 直接判失败。

比较对象：git 里的基准版本（默认 HEAD，即同步开始时的 main）与工作区。
只看 rule/ 与 module/：stash/、mrs/、config/ 下的产物都由它们派生，内容一一对应。

用法：
    python tools/sync-report.py                  # 本地跑完 sync-rules.py 后预览，打印到终端
    python tools/sync-report.py --out report.md --risk-out risk.md --github-output   # sync.yml 用
"""
import argparse
import collections
import io
import ipaddress
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CIDR_TYPES = ('IP-CIDR', 'IP-CIDR6', 'IP6-CIDR')
CIDR_MIN_PREFIX = {4: 8, 6: 16}           # 前缀短于它就算过宽（现有最宽的公网段是 17.0.0.0/8、240e::/18）
AREA_TYPES = ('IP-ASN', 'GEOIP', 'GEOSITE')
MODULE_KEYS = ('hostname', 'force-http-engine-hosts')

SAMPLE = 10              # 每个文件列出的增删样例条数
BODY_LIMIT = 60000       # PR 描述上限 65536 字符，留出页眉的余量


def _git(root, *args):
    return subprocess.run(['git'] + list(args), cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout


def _base_files(root, base, prefix, ext):
    out = _git(root, 'ls-tree', '-r', '-z', '--name-only', base, '--', prefix)
    return {p for p in out.decode('utf-8').split('\0') if p.endswith(ext)}


def _base_text(root, base, path):
    try:
        return _git(root, 'show', '%s:%s' % (base, path)).decode('utf-8', 'replace')
    except subprocess.CalledProcessError:
        return None                         # 基准里没有这个文件（新增）


def _work_files(root, prefix, ext):
    d = os.path.join(root, prefix)
    if not os.path.isdir(d):
        return set()
    return {prefix + f for f in os.listdir(d) if f.endswith(ext)}


def _work_text(root, path):
    p = os.path.join(root, path)
    if not os.path.isfile(p):
        return None                         # 工作区里删掉了
    return io.open(p, encoding='utf-8').read()


def rule_lines(text):
    """规则文件里的规则行（去掉注释与空行），保持原顺序并去重。"""
    seen = collections.OrderedDict()
    for ln in (text or '').split('\n'):
        s = ln.strip()
        if s and not s.startswith('#'):
            seen[s] = None
    return list(seen)


def risky_rule(line):
    """一条新增规则的风险说明；不算高风险返回 None。"""
    parts = [p.strip() for p in line.split(',')]
    if len(parts) < 2:
        return None
    rtype, value = parts[0].upper(), parts[1]
    if rtype in CIDR_TYPES:
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError:
            return '无法解析的网段'
        limit = CIDR_MIN_PREFIX[net.version]
        return 'IPv%d 网段宽于 /%d' % (net.version, limit) if net.prefixlen < limit else None
    if rtype == 'DOMAIN-SUFFIX':
        return '裸顶级域后缀' if '.' not in value.strip('.') else None
    if rtype == 'DOMAIN-WILDCARD':
        # 除了最后一段，其余全是只由 * / ? 组成的通配，就是整个顶级域：*.com、*.*.xyz、*。
        # apple.* 这类「固定的名字 + 通配顶级域」不算：盖住的是同一个品牌，上游一直有。
        labels = value.split('.')
        fixed = [x for x in labels if x.strip('*?')]
        return '通配整个顶级域' if not fixed or fixed == labels[-1:] else None
    if rtype in AREA_TYPES:
        return '整片地址或地区规则'
    return None


def module_hosts(text):
    """{键: 主机集合}：模块里的 MITM hostname 与 force-http-engine-hosts。"""
    out = {k: set() for k in MODULE_KEYS}
    for ln in (text or '').split('\n'):
        m = re.match(r'\s*(%s)\s*=\s*(.*)$' % '|'.join(MODULE_KEYS), ln)
        if not m:
            continue
        value = re.sub(r'^%(?:APPEND|INSERT)%\s*', '', m.group(2).strip())
        out[m.group(1)].update(h.strip() for h in value.split(',') if h.strip())
    return out


def module_rewrites(text):
    return sum(1 for ln in (text or '').split('\n') if ' - reject' in ln)


def collect(base='HEAD', root=ROOT):
    """返回 (规则文件行, 模块行, 高风险项)。

    规则文件行 = (路径, 原条数, 新条数, 新增列表, 删除列表)，只含有变化的文件；
    高风险项 = (路径, 内容, 原因)。
    """
    rules, modules, risks = [], [], []
    paths = _base_files(root, base, 'rule/', '.list') | _work_files(root, 'rule/', '.list')
    for path in sorted(paths):
        old = rule_lines(_base_text(root, base, path))
        new = rule_lines(_work_text(root, path))
        old_set, new_set = set(old), set(new)
        added = [x for x in new if x not in old_set]
        removed = [x for x in old if x not in new_set]
        if not added and not removed:
            continue
        rules.append((path, len(old), len(new), added, removed))
        for line in added:
            why = risky_rule(line)
            if why:
                risks.append((path, line, why))

    paths = (_base_files(root, base, 'module/', '.sgmodule')
             | _work_files(root, 'module/', '.sgmodule'))
    for path in sorted(paths):
        old_text, new_text = _base_text(root, base, path), _work_text(root, path)
        if old_text == new_text:
            continue
        old_h, new_h = module_hosts(old_text), module_hosts(new_text)
        modules.append((path, module_rewrites(old_text), module_rewrites(new_text),
                        len(old_h['hostname']), len(new_h['hostname'])))
        for key in MODULE_KEYS:
            for host in sorted(new_h[key] - old_h[key]):
                risks.append((path, '%s: %s' % (key, host), '新增解密或强制 HTTP 引擎的主机'))
    return rules, modules, risks


def _fence(lines):
    return '```\n%s\n```' % '\n'.join(lines)


def render(rules, modules, risks):
    """返回 (PR 描述用的 Markdown, 高风险项的 Markdown 列表)。"""
    risk_md = '\n'.join('- `%s` %s：`%s`' % (path, why, what) for path, what, why in risks)
    out = ['### 同步摘要', '']
    if rules:
        old_total = sum(r[1] for r in rules)
        new_total = sum(r[2] for r in rules)
        out.append('变动的规则文件 %d 个，合计 %s → %s 条（新增 %s，删除 %s）。'
                   'stash/、mrs/、config/ 下的产物随之重新生成。'
                   % (len(rules), format(old_total, ','), format(new_total, ','),
                      format(sum(len(r[3]) for r in rules), ','),
                      format(sum(len(r[4]) for r in rules), ',')))
        out += ['', '| 文件 | 原条数 | 新条数 | 新增 | 删除 |', '|---|---:|---:|---:|---:|']
        for path, old_n, new_n, added, removed in rules:
            out.append('| `%s` | %s | %s | %d | %d |'
                       % (path, format(old_n, ','), format(new_n, ','), len(added), len(removed)))
    else:
        out.append('规则文件没有变化。')
    for path, old_rw, new_rw, old_mitm, new_mitm in modules:
        out += ['', '模块 `%s`：拦截规则 %d → %d 条，解密主机 %d → %d 个。'
                % (path, old_rw, new_rw, old_mitm, new_mitm)]
    out.append('')
    if risks:
        out += ['#### 需要人工审核（%d 项）' % len(risks), '',
                '命中下列任一项就不自动合并：确认是上游有意的改动后手动合并，'
                '否则在 `tools/exclude.txt` 剔除或调整同步源。', '', risk_md]
    else:
        out += ['#### 没有需要人工审核的变更', '',
                '没有新增解密主机、过宽网段、裸顶级域或整片规则；validate 通过后自动合并。']
    if rules:
        out += ['', '<details><summary>增删样例（每个文件最多 %d 条）</summary>' % SAMPLE, '']
        for path, _, _, added, removed in rules:
            out += ['**%s**' % path, '']
            if added:
                out += ['新增 %d 条：' % len(added), '', _fence(added[:SAMPLE]), '']
            if removed:
                out += ['删除 %d 条：' % len(removed), '', _fence(removed[:SAMPLE]), '']
        out.append('</details>')
    body = '\n'.join(out) + '\n'
    if len(body) > BODY_LIMIT:
        cut = body.rfind('\n', 0, BODY_LIMIT - 200)
        body = body[:cut] + '\n\n……摘要过长已截断，完整内容见运行页的 Summary。\n'
        if '<details>' in body and '</details>' not in body:
            body += '</details>\n'
    return body, (risk_md + '\n' if risk_md else '')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description='同步 PR 的摘要与高风险变更')
    ap.add_argument('--base', default='HEAD', help='比较的基准版本（默认 HEAD）')
    ap.add_argument('--out', help='摘要写到这个文件（默认打印）')
    ap.add_argument('--risk-out', help='高风险项写到这个文件（没有时写空文件）')
    ap.add_argument('--github-output', action='store_true',
                    help='把 high_risk=0/1 追加到 $GITHUB_OUTPUT')
    args = ap.parse_args(argv)

    rules, modules, risks = collect(args.base)
    body, risk_md = render(rules, modules, risks)
    if args.out:
        with io.open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(body)
        print('摘要写入 %s：变动规则文件 %d 个，需要人工审核 %d 项' % (args.out, len(rules), len(risks)))
        for path, what, why in risks:
            print('  审核  %s %s：%s' % (path, why, what))
    else:
        print(body)
    if args.risk_out:
        with io.open(args.risk_out, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(risk_md)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary and args.out:
        with io.open(summary, 'a', encoding='utf-8') as fh:
            fh.write(body + '\n')
    if args.github_output:
        with io.open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as fh:
            fh.write('high_risk=%d\n' % (1 if risks else 0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
