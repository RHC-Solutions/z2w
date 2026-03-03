.PHONY: qa python-check explorer-lint

qa: python-check explorer-lint

python-check:
	python -m compileall .

explorer-lint:
	cd explorer && npm ci && npm run lint
