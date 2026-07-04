IMAGE ?= pcai-precheck
TAG   ?= 0.2.0
RELEASE ?= precheck
NAMESPACE ?= precheck

.PHONY: help lint run build push template install upgrade uninstall

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

lint: ## shellcheck + helm lint
	shellcheck scripts/precheck.sh
	helm lint helm/pcai-precheck

run: ## Run the Flask app locally (needs requirements installed)
	PORT_HTTP=18080 python -m app.main

run-script: ## Run the standalone bash script against config/domains.txt
	DOMAINS_FILE=./config/domains.txt ./scripts/precheck.sh

build: ## Build the docker image
	docker build -t $(IMAGE):$(TAG) .

push: ## Push the docker image
	docker push $(IMAGE):$(TAG)

docker-run: ## Run the container locally on port 18080
	docker run --rm -p 18080:18080 $(IMAGE):$(TAG)

template: ## Render the helm chart
	helm template $(RELEASE) helm/pcai-precheck \
	  --set image.repository=$(IMAGE) --set image.tag=$(TAG)

install upgrade: ## Install / upgrade the helm release
	helm upgrade --install $(RELEASE) helm/pcai-precheck \
	  --namespace $(NAMESPACE) --create-namespace \
	  --set image.repository=$(IMAGE) --set image.tag=$(TAG)

uninstall: ## Remove the helm release
	helm uninstall $(RELEASE) --namespace $(NAMESPACE)

pf port-forward: ## Port-forward the web UI to localhost:18080
	kubectl -n $(NAMESPACE) port-forward svc/$(RELEASE)-pcai-precheck 18080:18080
