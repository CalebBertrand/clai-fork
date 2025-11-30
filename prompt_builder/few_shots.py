# file: CLAI/prompt_builder/few_shots.py

FEW_SHOTS = [
    {
        "role": "user",
        "content": "list python files larger than 10 MB modified this week",
    },
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "tool_1",
                "type": "function",
                "function": {
                    "name": "emit_plan_v1",
                    "arguments": '{"version": "1.0", "intent": "file_search", "command": ["find", ".", "-type f", "-name \'*.py\'", "-mtime -7", "-size +10M", "-print"], "cwd": ".", "inputs": [], "outputs": [], "explain": "Find *.py changed in 7 days and larger than 10MB.", "needs_clarification": false, "question": ""}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "tool_1", "content": "Plan executed successfully"},
    {
        "role": "user",
        "content": "delete the big logs",
    },
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "tool_2",
                "type": "function",
                "function": {
                    "name": "emit_plan_v1",
                    "arguments": '{"version": "1.0", "intent": "file_delete", "command": ["echo" "\'awaiting confirmation\'"], "cwd": ".", "inputs": [], "outputs": [], "explain": "Deletion is destructive; request scope/confirmation first.", "needs_clarification": true, "question": "Which directory and size threshold? Preview first?"}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "tool_2", "content": "Plan executed successfully"},
]
