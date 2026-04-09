# Variables
REMOTE_USER = root
REMOTE_HOST = tgmx
REMOTE_PATH = /var/www/troparia-bot

.PHONY: deploy logs status build stop

deploy:
	git push
	ssh $(REMOTE_HOST) "cd $(REMOTE_PATH) && git pull && docker compose up -d --build"

logs:
	ssh $(REMOTE_HOST) "cd $(REMOTE_PATH) && docker compose logs -f bot"

status:
	ssh $(REMOTE_HOST) "cd $(REMOTE_PATH) && docker compose ps"

build:
	docker compose build

stop:
	ssh $(REMOTE_HOST) "cd $(REMOTE_PATH) && docker compose stop"
