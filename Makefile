SHELL := /bin/bash
VENV := venv
ACTIVATE_VENV := source $(VENV)/bin/activate
PACKAGE := jupysql
VERSION := $(shell bin/print-version)

.PHONY: all
all: clean launch

# Virtual Environments
.make.install:
	rm -rf $(VENV) .make.install*
	python3 -m venv $(VENV)
	$(ACTIVATE_VENV) && pip install build 'jupyterlab>=4.0.0,<5'
	$(ACTIVATE_VENV) && pip install -e .
	touch $@

.make.install.dev:
	rm -rf $(VENV) .make.install*
	python3 -m venv $(VENV)
	$(ACTIVATE_VENV) && pip install -e .[DEV]
	touch $@

# Development
.PHONY: wheel
wheel: dist/$(PACKAGE)-$(VERSION)-py3-none-any.whl

dist/$(PACKAGE)-$(VERSION)-py3-none-any.whl dist/$(PACKAGE)-$(VERSION).tar.gz: .make.install
	$(ACTIVATE_VENV) && python3 -m build
	mv dist/$(PACKAGE)-*-py3-none-any.whl dist/$(PACKAGE)-$(VERSION)-py3-none-any.whl
	mv dist/$(PACKAGE)-*.tar.gz dist/$(PACKAGE)-$(VERSION).tar.gz

.PHONY: launch
launch: .make.install
	$(ACTIVATE_VENV) && jupyter-lab

# Testing
.PHONY: tests
tests: test-lint test-unit

.PHONY: test-unit
test-unit: .make.install.dev
	$(ACTIVATE_VENV) && pytest --ignore=src/tests/integration

.PHONY: test-lint
test-lint: .make.install.dev
	$(ACTIVATE_VENV) && pkgmt lint src/

# Cleaning
.PHONY: clean
clean: clean-venv clean-build

.PHONY: clean-venv
clean-venv:
	rm -rf $(VENV) .make.install*

.PHONY: clean-build
clean-build:
	rm -rf dist/
