.PHONY: test deploy logs ps clean-test

test:
	docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm max-bot-test

clean-test:
	docker compose -f docker-compose.yml -f docker-compose.test.yml down -v

deploy: test
	git add .
	git commit -m "feat: integrate MAX messenger and Redis state management"
	git push origin main
	ssh tgmx "cd /var/www/troparia-bot && git pull && docker compose up -d --build"

logs:
	ssh tgmx "cd /var/www/troparia-bot && docker compose logs -f"

ps:
	ssh tgmx "cd /var/www/troparia-bot && docker compose ps"
