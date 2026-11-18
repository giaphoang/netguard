PY=.venv/bin/python
.PHONY: setup dashboard demo-up demo-down demo-test demo-replay demo-reset smoke
setup:
	uv venv --allow-existing --python 3.12 .venv
	uv pip install -r requirements-dev.txt
	cd web && npm ci

dashboard:
	cd web && npm run build

demo-up: dashboard
	@ctx=$$(scripts/local-context) && docker --context "$$ctx" compose -f fabric.yml up -d && NETGUARD_CONTEXT="$$ctx" $(PY) -m uvicorn api.fabric:app --host 127.0.0.1 --port 3000 --timeout-graceful-shutdown 5

demo-down:
	@ctx=$$(scripts/local-context) && docker --context "$$ctx" compose -f fabric.yml down

demo-reset:
	@ctx=$$(scripts/local-context) && docker --context "$$ctx" compose -f fabric.yml up -d --force-recreate

demo-replay: dashboard
	NETGUARD_REPLAY=1 $(PY) -m uvicorn api.fabric:app --host 127.0.0.1 --port 3000 --timeout-graceful-shutdown 5

demo-test: dashboard
	$(PY) -m pytest -q
	docker compose -f fabric.yml config --quiet

smoke:
	@ctx=$$(scripts/local-context) && NETGUARD_CONTEXT="$$ctx" $(PY) -m scripts.smoke
