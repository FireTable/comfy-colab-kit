#!/usr/bin/env bash
# Idempotent Cloudflare setup for comfy-colab-kit (ported from the original moss-colab-kit helper).
# Creates: one named tunnel  COLAB_TUNNEL_HOST -> http://127.0.0.1:8188 (cloudflared runs inside Colab),
#          a proxied CNAME for it, an Access service token for the gate, and an Access app on
#          COLAB_TUNNEL_HOST that ONLY admits that service token.
# The PUBLIC_HOST Access app (your e-mail allow list, OTP/Google/GitHub login) is created in the dashboard,
# see docs/CLOUDFLARE.md. Reads credentials from the environment, never prints them:
#   CLOUDFLARE_API_TOKEN (scopes: Account.Cloudflare Tunnel:Edit, Account.Access: Apps and Policies:Edit,
#                         Account.Access: Service Tokens:Edit, Zone.DNS:Edit)
#   ZONE_NAME (e.g. example.com), COLAB_TUNNEL_HOST (e.g. comfy-colab.example.com)
# Writes (0600): secrets/colab_tunnel_token, secrets/cf_service.json, secrets/cf_ids.json
set -euo pipefail
cd "$(dirname "$0")/.."
: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"; : "${ZONE_NAME:?}"; : "${COLAB_TUNNEL_HOST:?}"
TUNNEL_NAME="${TUNNEL_NAME:-comfy-colab}"; SVC_NAME="${SVC_NAME:-comfy-gate}"; TAG="comfy-colab-kit"
SEC=secrets; mkdir -p "$SEC"; chmod 700 "$SEC"; umask 077
API=https://api.cloudflare.com/client/v4
cf(){ curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" "$@"; }
ok(){ jq -e '.success' >/dev/null <<<"$1" || { echo "API error: $(jq -c '.errors' <<<"$1")" >&2; exit 1; }; }

R=$(cf "$API/zones?name=$ZONE_NAME"); ok "$R"
ZONE=$(jq -r '.result[0].id' <<<"$R"); ACCT=$(jq -r '.result[0].account.id' <<<"$R")

R=$(cf "$API/accounts/$ACCT/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false"); ok "$R"; TID=$(jq -r '.result[0].id // empty' <<<"$R")
if [[ -z "$TID" ]]; then R=$(cf -X POST "$API/accounts/$ACCT/cfd_tunnel" -d "{\"name\":\"$TUNNEL_NAME\",\"config_src\":\"cloudflare\"}"); ok "$R"; TID=$(jq -r '.result.id' <<<"$R"); fi
R=$(cf -X PUT "$API/accounts/$ACCT/cfd_tunnel/$TID/configurations" \
  -d "{\"config\":{\"ingress\":[{\"hostname\":\"$COLAB_TUNNEL_HOST\",\"service\":\"http://127.0.0.1:8188\"},{\"service\":\"http_status:404\"}]}}"); ok "$R"
R=$(cf "$API/accounts/$ACCT/cfd_tunnel/$TID/token"); ok "$R"; jq -r '.result' <<<"$R" > "$SEC/colab_tunnel_token"

body="{\"type\":\"CNAME\",\"name\":\"$COLAB_TUNNEL_HOST\",\"content\":\"$TID.cfargotunnel.com\",\"proxied\":true,\"comment\":\"$TAG\"}"
R=$(cf "$API/zones/$ZONE/dns_records?name=$COLAB_TUNNEL_HOST"); ok "$R"; RID=$(jq -r '.result[0].id // empty' <<<"$R")
if [[ -n "$RID" ]]; then
  [[ "$(jq -r '.result[0].comment // ""' <<<"$R")" == "$TAG" ]] || { echo "refusing to overwrite existing DNS record $COLAB_TUNNEL_HOST" >&2; exit 1; }
  R=$(cf -X PUT "$API/zones/$ZONE/dns_records/$RID" -d "$body")
else R=$(cf -X POST "$API/zones/$ZONE/dns_records" -d "$body"); fi
ok "$R"

R=$(cf "$API/accounts/$ACCT/access/service_tokens"); ok "$R"; SVC=$(jq -r --arg n "$SVC_NAME" '.result[]|select(.name==$n)|.id' <<<"$R" | head -1)
if [[ -z "$SVC" ]]; then R=$(cf -X POST "$API/accounts/$ACCT/access/service_tokens" -d "{\"name\":\"$SVC_NAME\",\"duration\":\"8760h\"}"); ok "$R"
elif [[ ! -f "$SEC/cf_service.json" ]]; then R=$(cf -X POST "$API/accounts/$ACCT/access/service_tokens/$SVC/rotate"); ok "$R"; else R=""; fi
if [[ -n "$R" ]]; then SVC=$(jq -r '.result.id' <<<"$R"); jq '{client_id:.result.client_id, client_secret:.result.client_secret}' <<<"$R" > "$SEC/cf_service.json"; fi

APP_NAME="$TAG-backend"
R=$(cf "$API/accounts/$ACCT/access/apps"); ok "$R"; APP=$(jq -r --arg n "$APP_NAME" '.result[]|select(.name==$n)|.id' <<<"$R" | head -1)
APPBODY=$(jq -n --arg n "$APP_NAME" --arg h "$COLAB_TUNNEL_HOST" --arg svc "$SVC" '{name:$n, type:"self_hosted", domain:$h,
  session_duration:"24h", app_launcher_visible:false,
  policies:[{name:"gate service token only", decision:"non_identity", include:[{service_token:{token_id:$svc}}], precedence:1}]}')
if [[ -n "$APP" ]]; then R=$(cf -X PUT "$API/accounts/$ACCT/access/apps/$APP" -d "$APPBODY"); else R=$(cf -X POST "$API/accounts/$ACCT/access/apps" -d "$APPBODY"); fi
ok "$R"; APP=$(jq -r '.result.id' <<<"$R")
jq -n --arg a "$ACCT" --arg z "$ZONE" --arg t "$TID" --arg app "$APP" --arg svc "$SVC" \
  '{account:$a, zone:$z, tunnel:$t, access_app:$app, service_token:$svc}' > "$SEC/cf_ids.json"
echo "Cloudflare ready: tunnel $TUNNEL_NAME -> $COLAB_TUNNEL_HOST. Secrets written to $SEC/ (not printed)."
