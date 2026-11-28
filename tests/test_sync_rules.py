# -*- coding: utf-8 -*-
"""sync-rules.py：上游规则的解析、黑名单匹配与缩水保护。"""
import io
import os


def test_parse_skips_comments_and_keeps_modifiers(sync_rules):
    body = '\n'.join([
        '# 注释',
        '',
        'HOST,a.example.com',
        'DOMAIN-SUFFIX,b.example.com,extended-matching,PROXY',
        'IP-CIDR,1.2.3.0/24',
        'IP-CIDR,5.6.7.0/24,no-resolve',
        'just-one-field',
    ])
    got = list(sync_rules.parse(body))
    assert got == [
        ('DOMAIN', 'a.example.com', ()),                              # HOST 归一成 DOMAIN
        ('DOMAIN-SUFFIX', 'b.example.com', ('extended-matching',)),  # 策略名丢掉，修饰符保留
        ('IP-CIDR', '1.2.3.0/24', ('no-resolve',)),                  # IP 类一律补 no-resolve
        ('IP-CIDR', '5.6.7.0/24', ('no-resolve',)),                  # 已有的不重复补
    ]


def test_excluded_by_exact_and_suffix_pattern(sync_rules):
    excludes = {
        ('DOMAIN', 'a.example.com'): None,          # 所有策略
        ('DOMAIN-SUFFIX', '*.cn'): 'game',          # 只对 game
    }
    eb = sync_rules.excluded_by
    assert eb(excludes, 'DOMAIN', 'a.example.com', 'proxy') == ('DOMAIN', 'a.example.com')
    assert eb(excludes, 'DOMAIN-SUFFIX', 'steam.example.cn', 'game') == ('DOMAIN-SUFFIX', '*.cn')
    assert eb(excludes, 'DOMAIN-SUFFIX', 'steam.example.cn', 'proxy') is None
    assert eb(excludes, 'DOMAIN', 'b.example.com', 'proxy') is None


def _write_rules(path, n):
    with io.open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('# 头部注释不计数\n')
        for i in range(n):
            fh.write('DOMAIN,host%d.example.com\n' % i)


def test_check_shrink_thresholds(sync_rules, tmp_path, monkeypatch):
    monkeypatch.setattr(sync_rules, 'OUTDIR', str(tmp_path))
    (a, slug_a), (b, slug_b), (c, _) = list(sync_rules.SLUG.items())[:3]
    _write_rules(os.path.join(str(tmp_path), slug_a + '.list'), 1000)
    _write_rules(os.path.join(str(tmp_path), slug_b + '.list'), 100)

    # 小幅减少：单个文件与合计都在阈值内
    assert sync_rules.check_shrink({a: ['x'] * 990, b: ['x'] * 95}) == []

    # 单个文件少了 60%（且超过最少 50 条）：报这个文件，合计也跟着超限
    problems = sync_rules.check_shrink({a: ['x'] * 1000, b: ['x'] * 40})
    assert any(('rule/%s.list' % slug_b) in p for p in problems)

    # 单个文件只少 6%（不到 10%），但合计少了 6% 以上：只报合计
    problems = sync_rules.check_shrink({a: ['x'] * 940, b: ['x'] * 100})
    assert len(problems) == 1 and problems[0].startswith('合计')

    # 磁盘上还没有文件的策略（新加的上游）不参与比较，哪怕一条都没有
    assert sync_rules.check_shrink({c: []}) == []
