SHELL := /bin/bash
VENV := venv
ACTIVATE_VENV := source $(VENV)/bin/activate
PACKAGE := jupysql
VERSION := $(shell bin/print-version)

.PHONY: all
all: clean install launch

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(ACTIVATE_VENV) && pip install 'jupyterlab>=4.0.0,<5' build

.PHONY: install
install: $(VENV)/bin/activate
	$(ACTIVATE_VENV) && pip install -e .

.PHONY: wheel
wheel: dist/$(PACKAGE)-$(VERSION)-py3-none-any.whl

dist/$(PACKAGE)-$(VERSION)-py3-none-any.whl dist/$(PACKAGE)-$(VERSION).tar.gz: $(VENV)/bin/activate
	$(ACTIVATE_VENV) && python3 -m build
	mv dist/$(PACKAGE)-*-py3-none-any.whl dist/$(PACKAGE)-$(VERSION)-py3-none-any.whl
	mv dist/$(PACKAGE)-*.tar.gz dist/$(PACKAGE)-$(VERSION).tar.gz

.PHONY: launch
launch:
	$(ACTIVATE_VENV) && jupyter-lab

.PHONY: clean
clean:
	rm -rf dist/ $(VENV)

