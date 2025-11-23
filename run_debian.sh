#!/bin/bash
# Run script for Debian/Ubuntu systems
# This script activates the virtual environment and starts the application

set -e  # Exit on error

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Error: Virtual environment not found!"
    echo "Please run setup_debian.sh first:"
    echo "  bash setup_debian.sh"
    exit 1
fi

echo "Starting Zendesk to Wasabi B2 Offloader..."
echo ""

# Activate virtual environment
source venv/bin/activate

# Upgrade pip and install/update requirements
echo "Updating dependencies..."
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Starting application..."
echo ""

# Run the application
python main.py
