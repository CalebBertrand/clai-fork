#!/bin/bash
# Launch the CLAI shell, sandboxing the directory you invoke it from.

BASEDIR=$(pwd)
SCRIPT=$(realpath "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

# Activate the project virtualenv if we're not already inside one
if [[ -z "$VIRTUAL_ENV" ]]; then
    if [[ -f "$SCRIPTPATH/venv/bin/activate" ]]; then
        echo "Activating virtual environment..."
        source "$SCRIPTPATH/venv/bin/activate"
    else
        echo "No venv found at $SCRIPTPATH/venv - run: python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
        exit 1
    fi
fi

cd "$SCRIPTPATH" || exit 1
exec python3 start_shell.py "$BASEDIR"
