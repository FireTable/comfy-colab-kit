#!/usr/bin/env bash
# Deploy apps/site to Cloudflare Pages.
# Reads configuration from .env (gitignored) if present, never hardcodes private domains.
# Automatically stamps content hashes (MD5) to bust browser asset caching.
#
# Environment variables (configured in .env):
#   SITE_PAGES_PROJECT   - Cloudflare Pages project name (default: comfy-colab-kit)
#   SITE_CUSTOM_DOMAIN   - Optional custom domain (e.g. site.example.com)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  # Safely load variables from .env without exporting everything
  eval "$(grep -E '^[A-Za-z0-9_]+=' .env | sed 's/^/export /')"
fi

PROJECT_NAME="${SITE_PAGES_PROJECT:-comfy-colab-kit}"
CUSTOM_DOMAIN="${SITE_CUSTOM_DOMAIN:-}"

if ! command -v wrangler >/dev/null 2>&1; then
  echo "Error: wrangler CLI not found. Install with: npm install -g wrangler" >&2
  exit 1
fi

echo "Stamping content hashes for cache-busting..."
python3 -c "
import hashlib, re

def hash_file(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:8]

h_css = hash_file('apps/site/styles.css')
h_js = hash_file('apps/site/main.js')

with open('apps/site/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'href=\"styles\.css(\?v=[^\"]*)?\"', f'href=\"styles.css?v={h_css}\"', content)
content = re.sub(r'src=\"main\.js(\?v=[^\"]*)?\"', f'src=\"main.js?v={h_js}\"', content)

with open('apps/site/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Asset hashes stamped: styles.css?v={h_css}, main.js?v={h_js}')
"

echo "Deploying apps/site to Cloudflare Pages (project: $PROJECT_NAME)..."
wrangler pages deploy apps/site --project-name "$PROJECT_NAME" --branch main

echo ""
echo "Deployment uploaded successfully!"
echo "Default Pages URL: https://${PROJECT_NAME}.pages.dev"

if [ -n "$CUSTOM_DOMAIN" ]; then
  echo "Custom domain configured: https://${CUSTOM_DOMAIN}"
fi
