#!/usr/bin/env bash
# 把 CI/CD 依赖的仓库设置一次性套上。这些是 GitHub 设置而不是仓库文件，
# 提交本身改不了，需要仓库管理员用自己的 gh 登录跑一次（重复跑无害）。
#
#   gh auth login            # 需要 repo 与 admin:repo_hook 权限
#   bash tools/apply-github-settings.sh [owner/repo]
#
# 做七件事：
#   1. secret scanning + push protection（公开仓库免费）。它认不得代理分享链接、
#      机场订阅与 MITM 私钥，那一层由 check-config.py 的 check_secrets 补。
#   2. 允许自动合并、合并后删分支——sync.yml 开的 PR 靠它在 validate 通过后自动合并。
#   3. 允许 GitHub Actions 创建 PR——否则 sync.yml 的 gh pr create 会被拒。
#      workflow 默认权限保持只读，需要写的 workflow 自己在 permissions 里声明。
#   4. 套上 .github/rulesets/main.json：main 要求 PR + validate 通过，禁止强推与删除。
#   5. 只允许 GitHub 官方的 action（actions/*），并要求钉完整的提交 SHA。
#      tag 可以被改指到别的提交，钉 SHA 才算真正锁住；Dependabot 每周提 PR 升级。
#      默认分支上还有没钉 SHA 的 workflow 时只做前半，SHA 要求等合并后再跑一次本脚本。
#   6. Dependabot 漏洞告警与安全更新（tools/requirements*.txt、workflow 里的 action）。
#   7. CodeQL 默认配置：扫描 tools/ 的 Python 与 .github/ 的 workflow。
set -euo pipefail
# Windows 控制台默认 cp1252/cp936，python 打印中文的 ruleset 名会抛 UnicodeEncodeError。
export PYTHONIOENCODING=utf-8

repo="${1:-adrianyusong/shadowrocket}"
here="$(cd "$(dirname "$0")/.." && pwd)"
ruleset="$here/.github/rulesets/main.json"

command -v gh >/dev/null || { echo "需要 GitHub CLI：https://cli.github.com/"; exit 2; }
gh auth status >/dev/null

# 读 workflow 文本，打印没钉 40 位提交 SHA 的 uses 行（本仓库内的 ./ 与 docker:// 不算）。
unpinned() {
  grep -E '^[[:space:]]*(-[[:space:]]+)?uses:' |
    grep -vE 'uses:[[:space:]]*(\./|docker://)' |
    grep -vE '@[0-9a-f]{40}([[:space:]]|$)' || true
}

echo "== 1/7 secret scanning + push protection"
gh api -X PATCH "repos/$repo" --input - >/dev/null <<'JSON'
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}}}
JSON

echo "== 2/7 自动合并、合并后删分支"
gh api -X PATCH "repos/$repo" -F allow_auto_merge=true -F delete_branch_on_merge=true >/dev/null

echo "== 3/7 允许 Actions 创建 PR（默认权限保持只读）"
gh api -X PUT "repos/$repo/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true >/dev/null

echo "== 4/7 ruleset：保护 main"
name="$(python -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['name'])" "$ruleset")"
id="$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" | head -n1)"
if [ -n "$id" ]; then
  gh api -X PUT "repos/$repo/rulesets/$id" --input "$ruleset" >/dev/null
  echo "   已更新 ruleset #$id"
else
  gh api -X POST "repos/$repo/rulesets" --input "$ruleset" >/dev/null
  echo "   已创建 ruleset「$name」"
fi

echo "== 5/7 只允许官方 action，并要求钉 SHA"
# 打开 SHA 要求后，任何一个 workflow 里没钉 SHA 的 action 都会让那次运行直接失败。
# 所以先查本地与默认分支上的全部 workflow，有漏网的就只限制来源，不开 SHA 要求。
branch="$(gh api "repos/$repo" --jq .default_branch)"
bad=""
for f in "$here"/.github/workflows/*.yml "$here"/.github/workflows/*.yaml; do
  [ -f "$f" ] || continue
  hits="$(unpinned < "$f")"
  [ -z "$hits" ] || bad+="   本地 ${f#"$here"/}："$'\n'"$hits"$'\n'
done
for f in $(gh api "repos/$repo/contents/.github/workflows?ref=$branch" \
             --jq '.[] | select(.type == "file") | .path'); do
  hits="$(gh api -H 'Accept: application/vnd.github.raw+json' "repos/$repo/contents/$f?ref=$branch" | unpinned)"
  [ -z "$hits" ] || bad+="   $branch 上的 $f："$'\n'"$hits"$'\n'
done
pinning=true
if [ -n "$bad" ]; then
  pinning=false
  printf '   这些 action 还没钉 SHA，暂不开 SHA 要求（合并钉 SHA 的改动后再跑一次本脚本）：\n%s' "$bad"
fi
gh api -X PUT "repos/$repo/actions/permissions" \
  -F enabled=true -f allowed_actions=selected -F sha_pinning_required="$pinning" >/dev/null
gh api -X PUT "repos/$repo/actions/permissions/selected-actions" --input - >/dev/null <<'JSON'
{"github_owned_allowed": true, "verified_allowed": false, "patterns_allowed": []}
JSON

echo "== 6/7 Dependabot 漏洞告警与安全更新"
gh api -X PUT "repos/$repo/vulnerability-alerts" >/dev/null
gh api -X PUT "repos/$repo/automated-security-fixes" >/dev/null

echo "== 7/7 CodeQL 默认配置"
# 首次打开会触发一次全量扫描；已经开着时重复设置无害。
gh api -X PATCH "repos/$repo/code-scanning/default-setup" \
  -f state=configured -f query_suite=default >/dev/null

echo
echo "完成。核对："
gh api "repos/$repo" --jq '"  secret scanning: \(.security_and_analysis.secret_scanning.status) / push protection: \(.security_and_analysis.secret_scanning_push_protection.status) / auto-merge: \(.allow_auto_merge)"'
gh api "repos/$repo/rulesets" --jq '.[] | "  ruleset #\(.id) \(.name): \(.enforcement)"'
gh api "repos/$repo/actions/permissions" --jq '"  actions: \(.allowed_actions) / 钉 SHA: \(.sha_pinning_required // "（未返回）")"'
gh api "repos/$repo/code-scanning/default-setup" --jq '"  CodeQL: \(.state) \(.languages // [] | join(", "))"'
