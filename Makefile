PYTHON ?= python

.PHONY: install test

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest
