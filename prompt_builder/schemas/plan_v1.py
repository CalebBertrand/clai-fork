PLAN_VERSION = "1.0"
PLAN_FN_NAME = "emit_plan_v1"

# Note: this schema is sent with "strict": true, which only accepts a subset of
# JSON Schema. Keywords such as "default", "minLength" and "minItems" are not
# part of that subset, so they are deliberately absent here - every property is
# required under strict mode anyway, and the executor validates the plan in
# _validate_basic().
PLAN_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "version",
        "intent",
        "command",
        "cwd",
        "inputs",
        "outputs",
        "explain",
        "needs_clarification",
        "question",
    ],
    "properties": {
        "version": {"type": "string", "enum": [PLAN_VERSION]},
        "intent": {"type": "string"},
        # One argv token per element: ["find", ".", "-name", "*.py"], not
        # pre-joined fragments like ["find", ". -name '*.py'"].
        "command": {"type": "array", "items": {"type": "string"}},
        "cwd": {"type": "string"},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "explain": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "question": {"type": "string"},
    },
}
