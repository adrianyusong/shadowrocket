#!/usr/bin/env bash
# CI 告警用的 issue：同一标题只留一个 open 的，之后的告警追加评论，恢复后自动关闭。
# 成功的运行没人会点开看，失败与需要人工处理的情况必须落到 issue 上；
# 而一直不关的旧 issue 会让人分不清问题还在不在，所以恢复时也要处理。
#
#   bash tools/ci-issue.sh open  "<标题>" <正文文件> [去重键]
#   bash tools/ci-issue.sh close "<标题>" "<评论>"
#
# open：有同标题的 open issue 就评论，没有就新开。给了去重键时，那个 issue 的正文或评论里
#       已经有这个键就什么也不做——validate.yml 的定时任务每天都跑，同一个问题不该每天评论
#       一次。键以 HTML 注释附在正文末尾，页面上看不见。
# close：关掉同标题的 open issue，没有就什么也不做。
#
# 需要 GH_TOKEN（issues: write）与 GH_REPO（或在仓库目录里运行），以及 jq。
# 标签默认 sync-alert（沿用最早只有同步流程告警时的名字），可用 ALERT_LABEL 改。
set -euo pipefail

action="${1:?用法：ci-issue.sh open|close <标题> <正文文件|评论> [去重键]}"
title="${2:?缺少标题}"
arg="${3:?缺少正文文件或评论}"
key="${4:-}"
label="${ALERT_LABEL:-sync-alert}"

find_issue() {
  gh issue list --label "$label" --state open --limit 100 --json number,title |
    jq -r --arg t "$title" '[.[] | select(.title == $t) | .number][0] // empty'
}

case "$action" in
  open)
    num="$(find_issue)"
    body="$arg"
    if [ -n "$key" ]; then
      marker="<!-- ci-issue:$key -->"
      if [ -n "$num" ]; then
        # 先取完再查：直接接管道给 grep -q，它一匹配就退出，gh 会因 SIGPIPE 失败。
        seen="$(gh issue view "$num" --json body,comments --jq '.body, .comments[].body')"
        if grep -qF -- "$marker" <<< "$seen"; then
          echo "#$num 已记录过这个问题（$key），不重复追加"
          exit 0
        fi
      fi
      body="$(mktemp)"
      { cat "$arg"; printf '\n%s\n' "$marker"; } > "$body"
    fi
    if [ -n "$num" ]; then
      gh issue comment "$num" --body-file "$body" > /dev/null
      echo "已在 #$num 追加：$title"
    else
      gh label create "$label" --color D93F0B --description "CI 的失败与告警" --force > /dev/null
      gh issue create --title "$title" --label "$label" --body-file "$body"
    fi
    ;;
  close)
    num="$(find_issue)"
    if [ -n "$num" ]; then
      gh issue close "$num" --comment "$arg" > /dev/null
      echo "已关闭 #$num：$title"
    fi
    ;;
  *)
    echo "未知动作：$action（应为 open 或 close）" >&2
    exit 2
    ;;
esac
