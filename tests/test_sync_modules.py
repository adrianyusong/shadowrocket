# -*- coding: utf-8 -*-
"""sync-modules.py：Clash Core 版本提醒。"""
import pytest


def test_core_version_gap(sync_modules):
    gap = sync_modules.core_version_gap
    assert gap('Clash Core v1.19.33-clash.5', 'v1.19.33') is None
    # 取页面上最新的一个；版本号按数字比较，不是按字符串（1.19.100 > 1.19.34）
    assert gap('v1.19.34-clash.3 … v1.19.33-clash.5', 'v1.19.33') == 'v1.19.34'
    assert gap('v1.19.100-clash.1 v1.19.34-clash.3', 'v1.19.34') == 'v1.19.100'
    assert gap('v1.19.32-clash.1', 'v1.19.33') == 'v1.19.32'       # MIHOMO_REF 跑到了设备前面


def test_core_version_gap_page_changed(sync_modules):
    with pytest.raises(ValueError):
        sync_modules.core_version_gap('<html>没有版本号了</html>')


def test_check_core_version_never_fails_sync(sync_modules, monkeypatch, tmp_path):
    alerts = tmp_path / 'alerts.md'
    monkeypatch.setenv('SYNC_ALERT_FILE', str(alerts))
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)

    def boom(url):
        raise OSError('network down')
    monkeypatch.setattr(sync_modules, 'fetch', boom)
    assert sync_modules.check_core_version() == 0 and not alerts.exists()

    monkeypatch.setattr(sync_modules, 'fetch', lambda url: 'v99.0.0-clash.1')
    assert sync_modules.check_core_version() == 0
    assert 'v99.0.0' in alerts.read_text(encoding='utf-8')
