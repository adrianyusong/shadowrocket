#!/usr/bin/env bash
# 把 CI/CD 依赖的仓库设置一次性套上。这些是 GitHub 设置而不是仓库文件，
# 提交本身改不了，需要仓库管理员用自己的 gh 登录跑一次（重复跑无害）。
#
#   gh auth login            # 需要 repo 与 admin:repo_hook 权限
#   bash tools/apply-github-settings.sh [owner/repo]
#
# 做四件事：
#   1. secret scanning + push protection（公开仓库免费）。它认不得代理分享链接、
#      机场订阅与 MITM 私钥，那一层由 check-config.py 的 check_secrets 补。
#   2. 允许自动合并、合并后删分支——sync.yml 开的 PR 靠它在 validate 通过后自动合并。
#   3. 允许 GitHub Actions 创建 PR——否则 sync.yml 的 gh pr create 会被拒。
#      workflow 默认权限保持只读，需要写的 workflow 自己在 permissions 里声明。
#   4. 套上 .github/rulesets/main.json：main 要求 PR + validate 通过，禁止强推与删除。
set -euo pipefail
# Windows 控制台默认 cp1252/cp936，python 打印中文的 ruleset 名会抛 UnicodeEncodeError。
export PYTHONIOENCODING=utf-8

repo="${1:-adrianyusong/shadowrocket}"
here="$(cd "$(dirname "$0")/.." && pwd)"
ruleset="$here/.github/rulesets/main.json"

command -v gh >/dev/null || { echo "需要 GitHub CLI：https://cli.github.com/"; exit 2; }
gh auth status >/dev/null

echo "== 1/4 secret scanning + push protection"
gh api -X PATCH "repos/$repo" --input - >/dev/null <<'JSON'
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}}}
JSON

echo "== 2/4 自动合并、合并后删分支"
gh api -X PATCH "repos/$repo" -F allow_auto_merge=true -F delete_branch_on_merge=true >/dev/null

echo "== 3/4 允许 Actions 创建 PR（默认权限保持只读）"
gh api -X PUT "repos/$repo/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true >/dev/null

echo "== 4/4 ruleset：保护 main"
name="$(python -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['name'])" "$ruleset")"
id="$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" | head -n1)"
if [ -n "$id" ]; then
  gh api -X PUT "repos/$repo/rulesets/$id" --input "$ruleset" >/dev/null
  echo "   已更新 ruleset #$id"
else
  gh api -X POST "repos/$repo/rulesets" --input "$ruleset" >/dev/null
  echo "   已创建 ruleset「$name」"
fi

echo
echo "完成。核对："
gh api "repos/$repo" --jq '"  secret scanning: \(.security_and_analysis.secret_scanning.status) / push protection: \(.security_and_analysis.secret_scanning_push_protection.status) / auto-merge: \(.allow_auto_merge)"'
gh api "repos/$repo/rulesets" --jq '.[] | "  ruleset #\(.id) \(.name): \(.enforcement)"'
