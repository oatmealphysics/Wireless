#!/usr/bin/env bash
# Publish main to the GitHub authority, then fast-forward the GitLab mirror to
# the exact GitHub tip. No force push or multi-push remote is used.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

mode="${1:-publish}"
if [[ "$mode" != "publish" && "$mode" != "--verify-only" ]]; then
  echo "usage: $0 [--verify-only]" >&2
  exit 2
fi

if [[ "$(git symbolic-ref --short HEAD)" != "main" ]]; then
  echo "error: publish from main" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is not clean" >&2
  exit 1
fi

mapfile -t origin_push_urls < <(git remote get-url --all --push origin)
mapfile -t mirror_push_urls < <(git remote get-url --all --push mirror)
if [[ "${#origin_push_urls[@]}" -ne 1 ]]; then
  echo "error: origin must have exactly one GitHub push URL" >&2
  exit 1
fi
case "${origin_push_urls[0]}" in
  git@github.com:oatmealphysics/Wireless.git|https://github.com/oatmealphysics/Wireless.git) ;;
  *)
    echo "error: origin must target oatmealphysics/Wireless on GitHub" >&2
    exit 1
    ;;
esac
if [[ "${#mirror_push_urls[@]}" -ne 1 ||
      "${mirror_push_urls[0]}" != "git@gitlab01.an.dd:user08/Wireless.git" ]]; then
  echo "error: mirror must have exactly one GitLab SSH push URL" >&2
  exit 1
fi

local_sha="$(git rev-parse refs/heads/main)"

if [[ "$mode" == "publish" ]]; then
  git push origin refs/heads/main:refs/heads/main
fi

origin_sha="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
if [[ "$origin_sha" != "$local_sha" ]]; then
  echo "error: GitHub main is $origin_sha, expected $local_sha" >&2
  exit 1
fi

if [[ "$mode" == "publish" ]]; then
  git push mirror refs/heads/main:refs/heads/main
fi

mirror_sha="$(git ls-remote mirror refs/heads/main | awk '{print $1}')"
if [[ "$mirror_sha" != "$origin_sha" ]]; then
  echo "error: GitLab main is $mirror_sha, expected GitHub $origin_sha" >&2
  exit 1
fi

printf 'verified origin/main == mirror/main == %s\n' "$origin_sha"
