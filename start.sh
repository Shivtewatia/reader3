#!/bin/bash
set -e

echo ""
echo " ========================================="
echo "   Reader3 | Your Personal Library"
echo " ========================================="
echo ""

# Add common uv install locations to PATH
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Load API key from any *.env file in this folder
for env_file in *.env .env; do
    if [ -f "$env_file" ]; then
        while IFS='=' read -r key value; do
            [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
            key=$(echo "$key" | xargs)
            value=$(echo "$value" | xargs)
            if [ "$key" = "ANTHROPIC_API_KEY" ] && [ -n "$value" ]; then
                export ANTHROPIC_API_KEY="$value"
                echo " API key loaded from $env_file"
            fi
        done < "$env_file"
        break
    fi
done

# Install uv if missing
if ! command -v uv &>/dev/null; then
    echo " [1/3] Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo ""
        echo " ERROR: Could not install uv. Install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
    echo " [1/3] uv installed successfully."
else
    echo " [1/3] uv found."
fi

echo " [2/3] Setting up dependencies..."
uv sync --quiet
echo " [2/3] Dependencies ready."

echo " [3/3] Starting server... your browser will open automatically."
echo ""
echo " Keep this window open while using the app in your browser."
echo " To stop: press Ctrl+C"
echo ""

uv run server.py
