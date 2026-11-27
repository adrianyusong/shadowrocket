# -*- coding: utf-8 -*-
"""tests/ 的公共夹具。

tools/ 下的脚本文件名带连字符，不能直接 import，这里按路径载入。每个测试拿到的都是
重新执行过的模块：check-config.py 用模块级的 FAILS / WARNS 收集结果，共用一份会让
测试之间互相污染。

本地运行：pip install -r tools/requirements-dev.txt && python -m pytest tests
"""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_tool(name):
    path = os.path.join(ROOT, 'tools', name + '.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sync_rules():
    return load_tool('sync-rules')


@pytest.fixture
def sync_modules():
    return load_tool('sync-modules')


@pytest.fixture
def sync_report():
    return load_tool('sync-report')


@pytest.fixture
def check_config():
    return load_tool('check-config')


@pytest.fixture
def check_mihomo():
    return load_tool('check-mihomo')
