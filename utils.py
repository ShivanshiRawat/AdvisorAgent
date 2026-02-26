"""
Shared utilities for the VIA agent.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first valid JSON object from text.

    Tries (in order):
    1. ```json ... ``` fenced blocks
    2. Balanced-brace scanning
    3. Entire text as JSON
    """
    if not text or not text.strip():
        return None
    text = text.strip()

    # Try fenced code blocks
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Balanced brace matching
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if start == -1:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
                    depth = 0

    # Last resort
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
