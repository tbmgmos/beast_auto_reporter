# ============================================================================
# Beast Auto Reporter — Makefile
# Управление ARM и Intel (x86_64) окружениями
# ============================================================================

.PHONY: help setup-arm setup-intel run-arm run-intel test-arm test-intel \
        verify-arm verify-intel test-both clean-arm clean-intel clean-all

SHELL := /bin/bash

# ── Help ───────────────────────────────────────────────────────────────────

help: ## Показать справку
	@echo "Beast Auto Reporter — Управление окружениями"
	@echo ""
	@echo "Установка:"
	@echo "  make setup-arm       Настроить ARM (Apple Silicon) окружение"
	@echo "  make setup-intel     Настроить Intel (x86_64) окружение"
	@echo ""
	@echo "Запуск:"
	@echo "  make run-arm         Запустить приложение (ARM)"
	@echo "  make run-intel       Запустить приложение (Intel/Rosetta 2)"
	@echo ""
	@echo "Тестирование:"
	@echo "  make test-arm        Тесты на ARM"
	@echo "  make test-intel      Тесты на Intel (x86_64 эмуляция)"
	@echo "  make test-both       Тесты на обеих архитектурах"
	@echo ""
	@echo "Проверка:"
	@echo "  make verify-arm      Проверить ARM окружение"
	@echo "  make verify-intel    Проверить Intel окружение"
	@echo ""
	@echo "Очистка:"
	@echo "  make clean-arm       Удалить ARM venv"
	@echo "  make clean-intel     Удалить Intel venv"
	@echo "  make clean-all       Удалить оба venv"

# ── Setup ──────────────────────────────────────────────────────────────────

setup-arm: ## Настроить ARM окружение
	@./scripts/setup_arm.sh

setup-intel: ## Настроить Intel окружение
	@./scripts/setup_intel.sh

# ── Run ────────────────────────────────────────────────────────────────────

run-arm: ## Запустить приложение (ARM)
	@./scripts/run_arm.sh

run-intel: ## Запустить приложение (Intel)
	@./scripts/run_intel.sh

# ── Test ───────────────────────────────────────────────────────────────────

test-arm: ## Тесты на ARM
	@./scripts/run_arm.sh --test

test-intel: ## Тесты на Intel
	@./scripts/run_intel.sh --test

test-both: ## Тесты на обеих архитектурах
	@echo "═══ ARM (arm64) ═══"
	@./scripts/run_arm.sh --test || true
	@echo ""
	@echo "═══ Intel (x86_64) ═══"
	@./scripts/run_intel.sh --test

# ── Verify ─────────────────────────────────────────────────────────────────

verify-arm: ## Проверить ARM окружение
	@./scripts/run_arm.sh --verify

verify-intel: ## Проверить Intel окружение
	@./scripts/run_intel.sh --verify

# ── Clean ──────────────────────────────────────────────────────────────────

clean-arm: ## Удалить ARM venv
	@echo "Удаляю venv/..."
	@rm -rf venv/

clean-intel: ## Удалить Intel venv
	@echo "Удаляю venv_x86/..."
	@rm -rf venv_x86/

clean-all: clean-arm clean-intel ## Удалить оба venv
	@echo "Все окружения удалены"
