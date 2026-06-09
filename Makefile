PYTHON ?= python3

.PHONY: help generate check-catalog test-python test-rust test check build build-release sync inspect-mcp package-addon package-addon-multiplatform clean

help:
	$(PYTHON) scripts/build.py --help

generate:
	$(PYTHON) scripts/build.py generate

check-catalog:
	$(PYTHON) scripts/build.py check-catalog

test-python:
	$(PYTHON) scripts/build.py test-python

test-rust:
	$(PYTHON) scripts/build.py test-rust

test check:
	$(PYTHON) scripts/build.py test

build:
	$(PYTHON) scripts/build.py build

build-release:
	$(PYTHON) scripts/build.py build-release

sync:
	$(PYTHON) scripts/build.py sync

inspect-mcp:
	$(PYTHON) scripts/build.py inspect-mcp

package-addon:
	$(PYTHON) scripts/build.py package-addon

package-addon-multiplatform:
	$(PYTHON) scripts/build.py package-addon --skip-build \
		--companion-binary macos-aarch64=artifacts/companions/companion-macos-aarch64/deckhand-server \
		--companion-binary macos-x86_64=artifacts/companions/companion-macos-x86_64/deckhand-server \
		--companion-binary linux-x86_64=artifacts/companions/companion-linux-x86_64/deckhand-server \
		--companion-binary windows-x86_64=artifacts/companions/companion-windows-x86_64/deckhand-server.exe

clean:
	$(PYTHON) scripts/build.py clean
