.PHONY: test deploy logs ps

test:
	python3 -m pytest --cov=. tests/

deploy: test
	git add .
	git commit -m "feat: integrate MAX messenger and Redis state management"
	git push origin main
	ssh tgmx "cd /var/www/troparia-bot && git pull && docker compose up -d --build"

logs:
	ssh tgmx "cd /var/www/troparia-bot && docker compose logs -f"

ps:
	ssh tgmx "cd /var/www/troparia-bot && docker compose ps"
