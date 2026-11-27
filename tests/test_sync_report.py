# -*- coding: utf-8 -*-
"""sync-report.py：同步 PR 的风险闸门。漏判就会把危险变更自动合并进 main。"""
import io
import os
import subprocess

import pytest


@pytest.mark.parametrize('line, risky', [
    ('IP-CIDR,0.0.0.0/1,no-resolve', True),          # 半个 IPv4 空间
    ('IP-CIDR,17.0.0.0/8,no-resolve', False),        # 仓库里已有的最宽公网段，刚好不算
    ('IP-CIDR,1.2.3.0/24,no-resolve', False),
    ('IP-CIDR6,2000::/3,no-resolve', True),
    ('IP-CIDR6,240e::/18,no-resolve', False),
    ('IP-CIDR,not-a-network,no-resolve', True),      # 解析不了的一律交给人看
    ('DOMAIN-SUFFIX,xyz', True),                     # 裸顶级域
    ('DOMAIN-SUFFIX,.xyz', True),
    ('DOMAIN-SUFFIX,example.xyz', False),
    ('DOMAIN-WILDCARD,*.com', True),
    ('DOMAIN-WILDCARD,*.*.xyz', True),
    ('DOMAIN-WILDCARD,*.example.com', False),
    ('IP-ASN,4134,no-resolve', True),
    ('GEOIP,CN', True),
    ('GEOSITE,google', True),
    ('DOMAIN,example.com', False),
    ('USER-AGENT,Example*', False),
    ('garbage', False),
])
def test_risky_rule(sync_report, line, risky):
    assert bool(sync_report.risky_rule(line)) is risky


def test_module_hosts_strips_append_marker(sync_report):
    text = '\n'.join([
        '[General]',
        'force-http-engine-hosts = %APPEND% a.example.com, b.example.com',
        '[MITM]',
        'hostname = %INSERT% c.example.com,d.example.com',
    ])
    got = sync_report.module_hosts(text)
    assert got['force-http-engine-hosts'] == {'a.example.com', 'b.example.com'}
    assert got['hostname'] == {'c.example.com', 'd.example.com'}


def _git(root, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                    '-c', 'commit.gpgsign=false'] + list(args),
                   cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _write(root, path, text):
    full = os.path.join(root, *path.split('/'))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with io.open(full, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


@pytest.fixture
def repo(tmp_path):
    """有一次提交的临时仓库：一个规则文件、一个模块。"""
    root = str(tmp_path)
    _git(root, 'init', '-q')
    _write(root, 'rule/direct.list', '# 头\nDOMAIN,a.example.com\nIP-CIDR,224.0.0.0/3,no-resolve\n')
    _write(root, 'module/m.sgmodule', '[MITM]\nhostname = %APPEND% a.example.com\n')
    _git(root, 'add', '-A')
    _git(root, 'commit', '-q', '-m', 'base')
    return root


def test_collect_only_reports_new_risks(sync_report, repo):
    # 已有的 224.0.0.0/3 早已审过，不重复报；新增的 0.0.0.0/1 与新解密主机要报。
    _write(repo, 'rule/direct.list',
           '# 头\nDOMAIN,a.example.com\nIP-CIDR,224.0.0.0/3,no-resolve\n'
           'DOMAIN,b.example.com\nIP-CIDR,0.0.0.0/1,no-resolve\n')
    _write(repo, 'rule/new.list', 'DOMAIN-SUFFIX,xyz\n')
    _write(repo, 'module/m.sgmodule', '[MITM]\nhostname = %APPEND% a.example.com, b.example.com\n')

    rules, modules, risks = sync_report.collect('HEAD', repo)
    assert [(r[0], r[1], r[2]) for r in rules] == [('rule/direct.list', 2, 4), ('rule/new.list', 0, 1)]
    assert sorted(what for _, what, _ in risks) == [
        'DOMAIN-SUFFIX,xyz', 'IP-CIDR,0.0.0.0/1,no-resolve', 'hostname: b.example.com']
    assert modules and modules[0][0] == 'module/m.sgmodule'

    body, risk_md = sync_report.render(rules, modules, risks)
    assert '需要人工审核（3 项）' in body
    assert risk_md.count('\n') == 3


def test_collect_no_change(sync_report, repo):
    assert sync_report.collect('HEAD', repo) == ([], [], [])
    body, risk_md = sync_report.render([], [], [])
    assert '没有需要人工审核的变更' in body and risk_md == ''


def test_collect_counts_deleted_file(sync_report, repo):
    os.remove(os.path.join(repo, 'rule', 'direct.list'))
    rules, _, risks = sync_report.collect('HEAD', repo)
    assert [(r[0], r[1], r[2]) for r in rules] == [('rule/direct.list', 2, 0)]
    assert risks == []


def test_main_writes_outputs(sync_report, tmp_path, monkeypatch):
    risks = [('rule/direct.list', 'IP-CIDR,0.0.0.0/1,no-resolve', 'IPv4 网段宽于 /8')]
    monkeypatch.setattr(sync_report, 'collect', lambda base: ([], [], risks))
    out, risk_out, gh_out = (str(tmp_path / n) for n in ('report.md', 'risk.md', 'output'))
    monkeypatch.setenv('GITHUB_OUTPUT', gh_out)
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)

    assert sync_report.main(['--out', out, '--risk-out', risk_out, '--github-output']) == 0
    assert io.open(gh_out, encoding='utf-8').read() == 'high_risk=1\n'
    assert '0.0.0.0/1' in io.open(risk_out, encoding='utf-8').read()
    assert '需要人工审核（1 项）' in io.open(out, encoding='utf-8').read()

    monkeypatch.setattr(sync_report, 'collect', lambda base: ([], [], []))
    assert sync_report.main(['--out', out, '--risk-out', risk_out, '--github-output']) == 0
    assert io.open(gh_out, encoding='utf-8').read() == 'high_risk=1\nhigh_risk=0\n'
    assert io.open(risk_out, encoding='utf-8').read() == ''


def test_render_truncates_long_body(sync_report):
    added = ['DOMAIN,host%05d.example.com' % i for i in range(20)]
    rules = [('rule/f%03d.list' % i, 0, 20, added, []) for i in range(400)]
    body, _ = sync_report.render(rules, [], [])
    assert len(body) <= sync_report.BODY_LIMIT
    assert '摘要过长已截断' in body and body.rstrip().endswith('</details>')
