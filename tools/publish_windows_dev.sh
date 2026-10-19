#!/usr/bin/env bash
# Keep public dev downloads available while replacing the Windows build.
set -euo pipefail

archive=${1:?usage: publish_windows_dev.sh ARCHIVE}
repo=${GITHUB_REPOSITORY:?}
commit=${GITHUB_SHA:?}
ref_name=${GITHUB_REF_NAME:?}

# Listing distinguishes an absent release from authentication/network failures.
release_id=$(gh api --paginate "repos/$repo/releases" --jq '.[] | select(.tag_name == "dev") | .id')
if [[ -z "$release_id" ]]; then
  gh release create dev --repo "$repo" --target "$commit" \
    --title 'Development build' --notes "Windows build of $commit ($ref_name)." \
    --prerelease --latest=false "$archive" "$archive.sha256"
  # An orphaned dev tag may predate the release we just created.
  gh api --method PATCH "repos/$repo/git/refs/tags/dev" -f sha="$commit" -F force=true
else
  # Upload first: a failed upload leaves the previous download available.
  gh release upload dev "$archive" "$archive.sha256" --repo "$repo" --clobber
  gh api --method PATCH "repos/$repo/git/refs/tags/dev" -f sha="$commit" -F force=true
  gh release edit dev --repo "$repo" --title 'Development build' \
    --notes "Windows build of $commit ($ref_name)." --prerelease --latest=false

  # Only remove superseded Windows assets; preserve assets from other workflows.
  assets=$(gh api --paginate "repos/$repo/releases/$release_id/assets" \
    --jq '.[] | [.id, .name] | @tsv')
  while IFS=$'\t' read -r asset_id asset_name; do
    case "$asset_name" in
      bookokrat-*-x86_64-pc-windows-gnu.zip|bookokrat-*-x86_64-pc-windows-gnu.zip.sha256)
        if [[ "$asset_name" != "$archive" && "$asset_name" != "$archive.sha256" ]]; then
          gh api --method DELETE "repos/$repo/releases/assets/$asset_id"
        fi
        ;;
    esac
  done <<< "$assets"
fi
