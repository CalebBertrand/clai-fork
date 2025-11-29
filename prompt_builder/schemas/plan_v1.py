PLAN_VERSION = "1.0"
PLAN_FN_NAME = "emit_plan_v1"

PLAN_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "intent", "command", "cwd", "inputs", "outputs", "explain", "needs_clarification", "question"],
    "properties": {
        "version": {"type": "string", "enum": [PLAN_VERSION]},
        "intent": {"type": "string", "minLength": 1},
        "command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "cwd": {"type": "string", "default": "."},
        "inputs": {"type": "array", "items": {"type": "string"}, "default": []},
        "outputs": {"type": "array", "items": {"type": "string"}, "default": []},
        "explain": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "question": {"type": "string"},
    },
}
