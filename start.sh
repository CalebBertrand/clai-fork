#!/bin/bash

BASEDIR=$(pwd)
SCRIPT=$(realpath "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

# Check if virtual environment is activated, activate if not
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo "Activating virtual environment..."
    source "$SCRIPTPATH/venv/bin/activate"
fi

# Change to parent directory and run the module
cd $SCRIPTPATH/..
python3 -m CLAI.start_shell $BASEDIR

