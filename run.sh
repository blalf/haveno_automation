#!/bin/bash
# Launch the Haveno Offer Automator
# Requires RetoSwap to be running with: --apiPort 3201 --apiPassword <yourpassword>
cd "$(dirname "$0")"

# Activate virtualenv if it exists
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

export PYTHONPATH="$PWD/src"
python3 src/app.py
