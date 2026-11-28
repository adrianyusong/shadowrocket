# -*- coding: utf-8 -*-
"""check-mihomo.py：配置改写、日志过滤、规则集核对，以及下载物与缓存的校验。"""
import gzip
import hashlib
import io
import os
import time
import zipfile

import pytest


def test_patch_config_moves_listeners_to_loopback(check_mihomo):
    text = '\n'.join([
        'mixed-port: 7890',
        'allow-lan: false',
        'dns:',
        '  enable: true',
        '  listen: 0.0.0.0:53',
        'geox-url:',
        '  geoip: https://cdn.example/geoip.dat',
        '  mmdb: https://cdn.example/country.mmdb',
    ])
    out, n = check_mihomo.patch_config(text, ports=(1001, 1002, 1003), secret='s3')
    lines = out.split('\n')
    # 配置里没有控制器：在文件头注入一个只绑 127.0.0.1 的
    assert lines[:2] == ['external-controller: 127.0.0.1:1002', 'secret: "s3"']
    assert 'mixed-port: 1001' in lines
    assert '  listen: 127.0.0.1:1003' in lines
    assert '  geoip: %sgeoip.dat' % check_mihomo.GEODATA_BASE in lines
    assert '  mmdb: %scountry.mmdb' % check_mihomo.GEODATA_BASE in lines
    assert n['geox-url'] == 2 and n['dns.listen'] == 1 and n['mixed-port'] == 1


def test_patch_config_without_ports_only_touches_geox(check_mihomo):
    text = 'mixed-port: 7890\ndns:\n  listen: 0.0.0.0:53\ngeox-url:\n  asn: https://x/a.mmdb\n'
    out, n = check_mihomo.patch_config(text)
    assert 'mixed-port: 7890' in out and '  listen: 0.0.0.0:53' in out
    assert 'external-controller' not in out
    assert dict(n) == {'geox-url': 1}


def test_log_problems_filters_allowlist_and_merges(check_mihomo):
    log = '\n'.join([
        'time="t" level=info msg="Start initial configuration in progress"',
        'time="t" level=warning msg="rule provider [a] failed"',
        'time="t" level=warning msg="rule provider [b] failed"',
        'time="t" level=error msg="parse classical rule [USER-AGENT,x*] error: '
        'unsupported rule type: USER-AGENT"',
        'time="t" level=error msg="something else"',
    ])
    assert sorted(check_mihomo._log_problems(log)) == [
        'error: something else',
        'warning: rule provider […] failed (×2)',
    ]


def test_compare_rule_counts(check_mihomo):
    expected = {'a': (10, 2), 'b': (5, 0), 'c': (3, 0), 'd': (4, 0)}
    api = {'a': {'ruleCount': 8}, 'b': {'ruleCount': 4}, 'd': {'ruleCount': 5}}
    probs, total = check_mihomo._compare(api, expected)
    assert total == 17
    assert len(probs) == 3
    assert any('规则集 b' in p and '少了 1 条' in p for p in probs)
    assert any('规则集 c 没有出现在内核里' in p for p in probs)
    assert any('规则集 d' in p and 'mrs/ 没跟着 stash/ 重新生成' in p for p in probs)


# ---- 下载物与缓存 ----

def _fake_archive(check_mihomo, payload):
    """当前平台的「发布资产」：Windows 是内含一个 .exe 的 zip，其余是 gzip。"""
    key = check_mihomo._platform()
    if key.startswith('windows'):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('mihomo-%s.exe' % key, payload)
        return buf.getvalue()
    return gzip.compress(payload)


@pytest.fixture
def fake_release(check_mihomo, monkeypatch):
    """把 PINS 换成假资产的哈希，_download 换成记录调用、写入指定内容的替身。"""
    good = _fake_archive(check_mihomo, b'trusted-binary')
    key = check_mihomo._platform()
    monkeypatch.setattr(check_mihomo, 'PINS', {
        check_mihomo.MIHOMO_REF: {key: hashlib.sha256(good).hexdigest()}})
    state = {'serve': good, 'calls': 0}

    def download(url, dest, tries=3):
        state['calls'] += 1
        with io.open(dest, 'wb') as fh:
            fh.write(state['serve'])
    monkeypatch.setattr(check_mihomo, '_download', download)
    return state


def _read(path):
    with io.open(path, 'rb') as fh:
        return fh.read()


def test_ensure_binary_reextracts_over_tampered_cache(check_mihomo, fake_release, tmp_path):
    cache = str(tmp_path)
    exe, _ = check_mihomo.ensure_binary(cache)
    assert _read(exe) == b'trusted-binary' and fake_release['calls'] == 1

    # 缓存里的可执行文件被换掉（缓存投毒），压缩包没动：照样用核对过的压缩包覆盖回来，不重新下载
    with io.open(exe, 'wb') as fh:
        fh.write(b'evil')
    exe, _ = check_mihomo.ensure_binary(cache)
    assert _read(exe) == b'trusted-binary' and fake_release['calls'] == 1


def test_ensure_binary_redownloads_bad_cached_archive(check_mihomo, fake_release, tmp_path):
    cache = str(tmp_path)
    exe, _ = check_mihomo.ensure_binary(cache)
    archive = [f for f in os.listdir(os.path.dirname(exe)) if f.endswith(('.gz', '.zip'))]
    assert len(archive) == 1
    with io.open(os.path.join(os.path.dirname(exe), archive[0]), 'wb') as fh:
        fh.write(b'corrupted')
    exe, _ = check_mihomo.ensure_binary(cache)
    assert _read(exe) == b'trusted-binary' and fake_release['calls'] == 2


def test_ensure_binary_refuses_wrong_hash(check_mihomo, fake_release, tmp_path):
    fake_release['serve'] = _fake_archive(check_mihomo, b'replaced-release-asset')
    with pytest.raises(check_mihomo.Failure, match='SHA-256'):
        check_mihomo.ensure_binary(str(tmp_path))
    folder = os.path.join(str(tmp_path), check_mihomo.MIHOMO_REF)
    assert [f for f in os.listdir(folder) if not f.endswith('.part')] == []   # 坏资产不留在缓存里


@pytest.fixture
def fake_geodata(check_mihomo, monkeypatch):
    """_download 的替身：资产给 content，.sha256sum 给 sum_of 的哈希。"""
    state = {'content': b'geo-v1', 'sum_of': b'geo-v1', 'calls': []}

    def download(url, dest, tries=3):
        state['calls'].append(url.rsplit('/', 1)[1])
        if url.endswith('.sha256sum'):
            data = ('%s  x\n' % hashlib.sha256(state['sum_of']).hexdigest()).encode()
        else:
            data = state['content']
        with io.open(dest, 'wb') as fh:
            fh.write(data)
    monkeypatch.setattr(check_mihomo, '_download', download)
    return state


def test_ensure_geodata_verifies_and_reuses_cache(check_mihomo, fake_geodata, tmp_path):
    got = check_mihomo.ensure_geodata(str(tmp_path), ['GeoSite.dat'])
    assert _read(got['GeoSite.dat']) == b'geo-v1'
    assert fake_geodata['calls'] == ['geosite.dat.sha256sum', 'geosite.dat']

    check_mihomo.ensure_geodata(str(tmp_path), ['GeoSite.dat'])     # 7 天内且校验和对得上：不下载
    assert len(fake_geodata['calls']) == 2

    with io.open(got['GeoSite.dat'], 'wb') as fh:                   # 缓存损坏：重新下载
        fh.write(b'trunc')
    check_mihomo.ensure_geodata(str(tmp_path), ['GeoSite.dat'])
    assert _read(got['GeoSite.dat']) == b'geo-v1' and len(fake_geodata['calls']) == 4


def test_ensure_geodata_refreshes_after_ttl(check_mihomo, fake_geodata, tmp_path):
    got = check_mihomo.ensure_geodata(str(tmp_path), ['GeoIP.dat'])
    old = time.time() - check_mihomo.GEODATA_TTL - 60
    os.utime(got['GeoIP.dat'], (old, old))
    fake_geodata['content'] = fake_geodata['sum_of'] = b'geo-v2'
    check_mihomo.ensure_geodata(str(tmp_path), ['GeoIP.dat'])
    assert _read(got['GeoIP.dat']) == b'geo-v2'


def test_ensure_geodata_fails_when_sum_never_matches(check_mihomo, fake_geodata, tmp_path):
    fake_geodata['sum_of'] = b'something-else'
    with pytest.raises(check_mihomo.Failure, match='sha256sum'):
        check_mihomo.ensure_geodata(str(tmp_path), ['ASN.mmdb'])
    assert len(fake_geodata['calls']) == 4                          # 整组重下过一次才放弃
