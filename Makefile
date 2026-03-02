.PHONY: test audit docs docs-serve docs-build clean-docs

test:
	pytest

audit:
	python tests/run_audit.py

docs:
	python docs/generate_audit.py
	python docs/generate_templates.py

docs-serve: docs
	mkdocs serve

docs-build: docs
	mkdocs build

clean-docs:
	rm -f docs/audit.md docs/template-reference.md
	rm -rf site/
