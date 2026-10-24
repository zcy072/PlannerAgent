"""Helpers for parsing compact JSON responses from LLM calls."""

import json
import re
from typing import Any, Dict, List, Optional


def parse_json_array(response: str, max_items: int, fallback: List[Any]) -> List[Any]:
    """Extract a JSON array from an LLM response."""
    try:
        text = response.strip()
        if "[" in text:
            start = text.find("[")
            end = text.rfind("]") + 1
            text = text[start:end]

        data = json.loads(text)
        if isinstance(data, list) and data:
            return data[:max_items]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return fallback


def parse_keywords(
    response: str,
    fallback: List[str],
    max_items: int = 5,
) -> List[str]:
    """Parse a JSON array of search keywords."""
    return [
        str(keyword)
        for keyword in parse_json_array(
            response,
            max_items=max_items,
            fallback=fallback,
        )
    ]


def parse_selected_indices(response: str, max_index: int, fallback_limit: int) -> List[int]:
    """Parse a JSON array of 1-based selected indexes."""
    raw_items = parse_json_array(
        response=response,
        max_items=max_index,
        fallback=list(range(1, min(fallback_limit + 1, max_index + 1))),
    )

    indices = []
    for item in raw_items:
        try:
            index = int(item)
        except (ValueError, TypeError):
            continue

        if 0 < index <= max_index:
            indices.append(index)

    if indices:
        return indices

    return list(range(1, min(fallback_limit + 1, max_index + 1)))


def extract_json_object(response: str) -> str:
    """Extract the first JSON object from plain text or a Markdown code block."""
    if not response:
        return ""

    text = response
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response, re.IGNORECASE)
    if fenced:
        text = fenced.group(1)

    start = text.find("{")
    if start < 0:
        return text

    brace_count = 0
    for index, char in enumerate(text[start:], start):
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0:
                return text[start:index + 1]

    return text[start:]


def parse_json_object(response: str) -> Optional[Dict[str, Any]]:
    """Parse the first JSON object in an LLM response."""
    try:
        data = json.loads(extract_json_object(response))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None
