# -*- coding: utf-8 -*-
"""check-config.py：自托管模块的白名单与凭据扫描器的自检。"""
import io
import os

import pytest

GOOD_MODULE = '\n'.join([
    '#!name=测试模块',
    '# 注释行随便写',
    '[General]',
    'force-http-engine-hosts = %APPEND% a.example.com',
    '',
    '[URL Rewrite]',
    r'^https?:\/\/a\.example\.com\/ad - reject',
    r'^https?:\/\/a\.example\.com\/img - reject-img',
    '',
    '[MITM]',
    'hostname = %APPEND% a.example.com',
    '',
])


def test_secret_scanner_selftest(check_config):
    # 扫描器自己坏了会静默放行一切；自检返回的是没按预期命中 / 放行的样本。
    assert check_config._secret_selftest() == []


def test_module_allowlist_accepts_pure_reject_module(check_config):
    assert check_config.module_problems(GOOD_MODULE, 'm.sgmodule') == []


@pytest.mark.parametrize('extra, why', [
    (r'^https?:\/\/a\.example\.com\/x https://evil.example 302', '跳转'),
    (r'^https?:\/\/a\.example\.com\/x header-replace User-Agent x', '改写请求头'),
    ('[Script]\nx = type=http-response,pattern=^https://a,script-path=https://evil/x.js', '脚本'),
    ('[Rule]\nDOMAIN,a.example.com,DIRECT', '不允许的段'),
    ('[MITM]\nca-passphrase = x', 'MITM 段的未知键'),
])
def test_module_allowlist_rejects(check_config, extra, why):
    problems = check_config.module_problems(GOOD_MODULE + extra + '\n', 'm.sgmodule')
    assert problems, why


def test_module_allowlist_rejects_content_before_any_section(check_config):
    body = r'^https?:\/\/a\.example\.com\/ad - reject' + '\n' + GOOD_MODULE
    assert any('段外' in p for p in check_config.module_problems(body, 'm.sgmodule'))


def test_check_modules_fails_on_bad_file(check_config, tmp_path):
    with io.open(os.path.join(str(tmp_path), 'good.sgmodule'), 'w', encoding='utf-8') as fh:
        fh.write(GOOD_MODULE)
    assert check_config.check_modules(str(tmp_path)) == 1
    assert check_config.FAILS == []

    with io.open(os.path.join(str(tmp_path), 'bad.sgmodule'), 'w', encoding='utf-8') as fh:
        fh.write(GOOD_MODULE + r'^https?:\/\/a\.example\.com\/x https://evil.example 302' + '\n')
    assert check_config.check_modules(str(tmp_path)) == 2
    assert len(check_config.FAILS) == 1 and 'bad.sgmodule' in check_config.FAILS[0]


def test_committed_modules_pass(check_config):
    assert check_config.check_modules() >= 1
    assert check_config.FAILS == []
