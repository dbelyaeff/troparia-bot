# Instruction for AI Agents

Welcome to the Troparia Bot project! To maintain consistency and reliable deployments, please follow these rules:

## 1. Documentation First
- Every change that affects functionality or deployment MUST be documented in `CHANGELOG.md`.
- Use Semantic Versioning for version increments.

## 2. Deployment Workflow
- **Production Server**: `tgmx`
- **Deployment Path**: `/var/www/troparia-bot/`
- **Method**: Git-based.
    1. Commit and push changes to GitHub.
    2. Run `make deploy` to trigger remote pull and container rebuild.
- **Environment Variables**: Never hardcode secrets. Use `.env` (not tracked in Git) and ensure new variables are added to the server's `.env` manually or via instructions.

## 3. Technology Stack
- **Runtime**: Docker (compose).
- **Network**: Service joins `caddy-network` for proxying.
- **Port**: Bot listens on port `8080` inside the container.

## 4. Safety
- Do not run `docker compose` locally while production is active if it shares resources or state (though currently stateless).
- Always check remote logs with `make logs` after deployment.

## 5. Webhook Security
- **Secret Validation**: All webhooks (Telegram and MAX) MUST validate a secret token provided in headers (`X-Telegram-Bot-Api-Secret-Token` for Telegram, `X-Max-Bot-Api-Secret` for MAX).
- **Auto-Setup**: Bots MUST automatically register/update their webhooks with the correct URL and secret upon startup.
