#!/bin/bash
# Copyright (c) 2026 Ravenkey LLC dba Helioryn. All rights reserved.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== ruff check ==="
ruff check src/ tests/

echo "=== ruff format ==="
ruff format --check src/ tests/

echo "=== pytest ==="
pytest tests/ -v --tb=short

echo "=== ALL CHECKS PASSED ==="
