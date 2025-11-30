# SPDX-License-Identifier: MIT
# file: clai/llm/translator.py

from __future__ import annotations
import json, time
from pathlib import Path
from typing import Optional, Dict, Any

from .adapter_openai import OpenAITranslator

def write_plan_to_file(plan: Dict[str, Any], outdir: Path = Path("plans")) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"plan_{int(time.time())}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

class Translator:
    """Backend-agnostic façade (currently OpenAI)."""
    def __init__(self, model: Optional[str] = None):
        self.backend = OpenAITranslator(model=model)

    def to_plan(self, nl_request: str, extra_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.backend.translate(nl_request, extra_context=extra_context).plan

    def to_file(self, nl_request: str, extra_context: Optional[Dict[str, Any]] = None,
                outdir: Path = Path("plans")) -> Path:
        plan = self.to_plan(nl_request, extra_context=extra_context)
        return write_plan_to_file(plan, outdir)
    
    def add_execution_context(self, context_info: str) -> None:
        """Add execution context to conversation history."""
        self.backend.add_execution_context(context_info)
