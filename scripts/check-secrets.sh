#!/usr/bin/env bash
# Fast local secret scan over tracked + staged files (CI additionally runs gitleaks on full history).
# Usage: scripts/check-secrets.sh [paths...]   — exits 1 on any hit, prints file:line only (never the value).
set -euo pipefail
cd "$(dirname "$0")/.." && cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PATTERNS='(hf_[A-Za-z0-9]{30,}|ya29\.[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{30,}\.eyJ[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"client_secret"\s*:\s*"[^"]{20,}"|CF_TUNNEL_TOKEN=[A-Za-z0-9+/=]{40,}|X-Auth-Key:\s*[0-9a-f]{37})'
FILES=()
if [ "$#" -gt 0 ]; then FILES=("$@"); else
  while IFS= read -r f; do FILES+=("$f"); done < <( { git ls-files; git diff --cached --name-only; } 2>/dev/null | sort -u)
fi
hits=0
for f in ${FILES[@]+"${FILES[@]}"}; do
  [ -f "$f" ] || continue
  case "$f" in *.png|*.jpg|*.webp|*.ico) continue;; esac
  if out=$(grep -nEo "$PATTERNS" "$f" 2>/dev/null | cut -d: -f1); [ -n "$out" ]; then
    for l in $out; do echo "possible secret: $f:$l"; done; hits=1
  fi
done
for bad in .env secrets data; do
  if git ls-files --error-unmatch "$bad" >/dev/null 2>&1 || git ls-files "$bad/" 2>/dev/null | grep -q .; then echo "forbidden path tracked: $bad"; hits=1; fi
done
if [ "$hits" = 0 ]; then echo "secret scan: clean (${#FILES[@]} files)"; fi
exit "$hits"
