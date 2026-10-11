# verify.mk: verify-kit make targets for a project verification skill.
# Vendored into skills/verify-<app>/verify.mk. Do not edit a vendored copy; change the origin
# (harnesses-private/nickromney/verify-kit) and re-vendor with bin/vendor.sh.
#
# Usage, in the repo Makefile:
#   VERIFY_SKILL := skills/verify-<app>
#   include $(VERIFY_SKILL)/verify.mk

VERIFY_SKILL ?= $(error set VERIFY_SKILL before including verify.mk)
VERIFY_BASH ?= /bin/bash
VERIFY_CONTROL = $(VERIFY_BASH) $(VERIFY_SKILL)/scripts/control.sh

.PHONY: verify verify-dry-run verify-test

verify: ## Prove the app end to end via the verification skill (launch, drive, cleanup)
	$(VERIFY_CONTROL) --execute verify

verify-dry-run: ## Show what `make verify` would do, without doing it
	$(VERIFY_CONTROL) --dry-run verify

verify-test: ## Lint and run the verification skill's BATS tests under Bash 3.2
	shellcheck -x -P $(VERIFY_SKILL)/scripts $(VERIFY_SKILL)/scripts/*.sh $(VERIFY_SKILL)/tests/*.bats
	bats $(VERIFY_SKILL)/tests
