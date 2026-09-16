#!/bin/bash
# Pre-commit hook to run tests and ruff

set -e

echo "🧪 Running tests..."
uv run pytest

echo "🖤 Running ruff format..."
if ! uv run ruff format . --check; then
    echo "❌ Ruff found formatting issues. To auto fix, run:"
    echo -e "\033[32muv run ruff format .\033[0m"
    exit 1
fi

echo "🧼 Running ruff check..."
if ! uv run ruff check .; then
    echo "❌ Ruff found linting issues. To auto fix, run:"
    echo -e "\033[32muv run ruff check . --fix\033[0m"
    exit 1
fi

echo "✅ Pre-commit checks passed!"
