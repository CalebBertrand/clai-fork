#!/bin/bash

# Check if virtual environment is activated, activate if not
if [[ "$VIRTUAL_ENV" == "" ]]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Change to parent directory and run the module
cd ..
python3 -m CLAI.start_shell