# -*- coding: utf-8 -*-
"""workflow 里那些写错了不会报错、只会悄悄失效的约定。"""
import io
import json
import os
import re

import pytest
import yaml

from conftest import ROOT, load_tool


def _workflow(name):
    with io.open(os.path.join(ROOT, '.github', 'workflows', name), encoding='utf-8') as fh:
        return yaml.safe_load(fh)


def _runs(job):
    return [s['run'].strip() for s in job['steps'] if 'run' in s]


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def test_required_check_name_matches_ruleset():
    with io.open(os.path.join(ROOT, '.github', 'rulesets', 'main.json'), encoding='utf-8') as fh:
        ruleset = json.load(fh)
    contexts = [c['context'] for r in ruleset['rules'] if r['type'] == 'required_status_checks'
                for c in r['parameters']['required_status_checks']]
    jobs = _workflow('validate.yml')['jobs']
    for ctx in contexts:
        # 检查名 = job id（没有 name、没有 matrix 时）。名字一变，PR 就会永远等一个不存在的检查。
        assert ctx in jobs, ctx
        assert 'name' not in jobs[ctx] and 'strategy' not in jobs[ctx], ctx


def test_windows_job_runs_the_same_commands():
    jobs = _workflow('validate.yml')['jobs']
    linux = [r for r in _runs(jobs['validate']) if r.startswith(('python ', 'pip '))]
    windows = [r for r in _runs(jobs['windows']) if r.startswith(('python ', 'pip '))]
    assert [r for r in linux if r != 'python tools/lint-workflows.py'] == windows


# 生成脚本 -> 它写出的产物（取脚本里的常量，不手抄路径）。
# sync.yml 的 build 新增生成步骤时，这里也要补上，测试会提醒。
def _outputs():
    sr, sm = load_tool('sync-rules'), load_tool('sync-modules')
    bs, bm, bc = load_tool('build-stash'), load_tool('build-mrs'), load_tool('build-clash')
    return {
        'sync-rules': [sr.OUTDIR + os.sep],
        'sync-modules': [sm.OUTDIR + os.sep],
        'build-stash': [bs.OUTDIR + os.sep, bs.OVERRIDE],
        'build-mrs': [bm.OUTDIR + os.sep],
        'build-clash': [bc.OUT],
    }


def test_sync_paths_cover_every_generator_output():
    wf = _workflow('sync.yml')
    registered = wf['env']['SYNC_PATHS'].split()
    outputs = _outputs()

    scripts = [m.group(1) for r in _runs(wf['jobs']['build'])
               for m in [re.match(r'python tools/((?:sync|build)-[a-z]+)\.py$', r)] if m]
    assert scripts, 'build job 里没找到生成步骤'
    for script in scripts:
        assert script in outputs, '新的生成步骤 %s：在本测试的 _outputs 与 SYNC_PATHS 里登记它的产物' % script

    for script, paths in outputs.items():
        for path in paths:
            rel = _rel(path.rstrip(os.sep)) + ('/' if path.endswith(os.sep) else '')
            covered = any(rel == p or (p.endswith('/') and rel.startswith(p)) for p in registered)
            assert covered, '%s 的产物 %s 没有登记在 sync.yml 的 SYNC_PATHS' % (script, rel)


def test_sync_build_job_has_no_write_access():
    wf = _workflow('sync.yml')
    assert wf['permissions'] == {}
    assert wf['jobs']['build']['permissions'] == {'contents': 'read'}
    for step in wf['jobs']['build']['steps']:
        assert 'GH_TOKEN' not in (step.get('env') or {}), step.get('name')
        if step.get('uses', '').startswith('actions/checkout@'):
            assert step['with']['persist-credentials'] is False


@pytest.mark.parametrize('name', ['sync.yml', 'validate.yml'])
def test_checkout_never_persists_credentials(name):
    for job_id, job in _workflow(name)['jobs'].items():
        for step in job['steps']:
            if step.get('uses', '').startswith('actions/checkout@'):
                assert step.get('with', {}).get('persist-credentials') is False, (name, job_id)
