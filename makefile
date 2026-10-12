.PHONY: test typecheck verify

test:
	.venv/bin/pytest -q

typecheck:
	.venv/bin/mypy src tests

verify: test typecheck
	.venv/bin/python -m compileall -q src tests
