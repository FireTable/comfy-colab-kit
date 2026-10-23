# Comfy Colab Kit — promo site

Static product page. Plain HTML, CSS and JS. No build step and no npm dependencies.

## Preview locally

```sh
python3 -m http.server -d site 8080
```

Then open http://localhost:8080.

Or: `npx serve site`.

## Cloudflare Pages

- Output directory: `site/`
- Build command: empty (pure static)

## Deploy

Documented only — do not run from CI unless you mean to publish:

```sh
npx wrangler pages deploy site --project-name <your-project-name>
```

## Before publishing

Replace `OWNER` with the real GitHub owner in both places:

- `site/index.html` — every `https://github.com/OWNER/comfy-colab-kit` href
- `site/main.js` — `const REPO_URL = "https://github.com/OWNER/comfy-colab-kit";`

Doc links follow `${REPO_URL}/blob/main/docs/AGENT.md` (and the same pattern for other docs).
