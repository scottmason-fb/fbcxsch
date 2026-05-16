#!/bin/bash

# CX Scheduler — Double-click this file to launch the app
# ─────────────────────────────────────────────────────────

cd "$(dirname "$0")"

echo ""
echo "========================================"
echo "  CX Scheduler — Starting up..."
echo "========================================"
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌  Python is not installed."
    echo ""
    echo "  Please download and install Python from:"
    echo "  https://www.python.org/downloads/"
    echo ""
    echo "  Then double-click this file again."
    echo ""
    read -p "Press Enter to close..."
    exit 1
fi

echo "✅  Python found: $(python3 --version)"
echo ""

# Install / update required packages
echo "📦  Installing required packages (first run may take a moment)..."
python3 -m pip install streamlit pandas --quiet --upgrade

echo ""
echo "✅  Packages ready."
echo ""
echo "🌐  Opening CX Scheduler in your browser..."
echo "    (To stop the app, press Control + C in this window)"
echo ""

# Launch the app
python3 -m streamlit run app.py \
    --server.headless false \
    --browser.gatherUsageStats false \
    --theme.primaryColor "#89AC9E" \
    --theme.backgroundColor "#FFF9F4" \
    --theme.secondaryBackgroundColor "#F6F5F4" \
    --theme.textColor "#1D2019"
