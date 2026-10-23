#!/usr/bin/env bash
# Deploy apps/site to Cloudflare Pages.
# Reads configuration from .env (gitignored) if present, never hardcodes private domains.
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

echo "Deploying apps/site to Cloudflare Pages (project: $PROJECT_NAME)..."
wrangler pages deploy apps/site --project-name "$PROJECT_NAME" --branch main

echo ""
echo "Deployment uploaded successfully!"
echo "Default Pages URL: https://${PROJECT_NAME}.pages.dev"

if [ -n "$CUSTOM_DOMAIN" ]; then
  echo "Custom domain configured: https://${CUSTOM_DOMAIN}"
fi
