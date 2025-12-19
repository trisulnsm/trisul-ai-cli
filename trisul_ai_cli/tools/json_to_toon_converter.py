"""
Production-grade JSON to TOON (Token-Oriented Object Notation) converter.
TOON is a compact, human-readable serialization format designed for LLM inputs.
Spec reference: https://github.com/toon-format/spec
"""

from __future__ import annotations
import re
import math
import json
from typing import Any, Literal
import ast

# Type aliases
JsonValue = None | bool | int | float | str | list[Any] | dict[str, Any]
Delimiter = Literal[",", "\t", "|"]

# Constants
IDENTIFIER_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')
NUMBER_PATTERN = re.compile(r'^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$')
RESERVED_LITERALS = frozenset({"true", "false", "null"})
ESCAPE_MAP = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def json_to_toon(
    data: Any,
    *,
    delimiter: Delimiter = ",",
    length_marker: bool = False,
    indent_size: int = 2,
) -> str:
    """
    Convert JSON-serializable data to TOON format.

    Args:
        data: JSON-serializable Python value (dict, list, str, int, float, bool, None)
        delimiter: Value delimiter - comma (default), tab, or pipe
        length_marker: If True, prefix array lengths with '#' (e.g., [#3] instead of [3])
        indent_size: Number of spaces per indentation level

    Returns:
        TOON-formatted string

    Raises:
        ValueError: If data contains circular references or unsupported types

    Examples:
        >>> json_to_toon({"name": "Alice", "age": 30})
        'name: Alice\\nage: 30'

        >>> json_to_toon([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
        '[2]{id,name}:\\n1,Alice\\n2,Bob'
    """
    
    
    # parse when input is a string
    data = _parse_input(data)
    
    ctx = _EncoderContext(
        delimiter=delimiter,
        length_marker="#" if length_marker else "",
        indent=" " * indent_size,
    )
    lines: list[str] = []
    _encode_value(data, lines, ctx, depth=0, is_root=True)
    return "\n".join(lines)


class _EncoderContext:
    """Internal context for encoding state."""
    __slots__ = ("delimiter", "length_marker", "indent", "_seen")

    def __init__(self, delimiter: Delimiter, length_marker: str, indent: str):
        self.delimiter = delimiter
        self.length_marker = length_marker
        self.indent = indent
        self._seen: set[int] = set()

    def check_circular(self, obj: Any) -> None:
        obj_id = id(obj)
        if obj_id in self._seen:
            raise ValueError("Circular reference detected")
        self._seen.add(obj_id)

    def uncheck(self, obj: Any) -> None:
        self._seen.discard(id(obj))



def _parse_input(value: Any) -> Any:
    """Parse string input as JSON or Python literal; pass through other types."""
    if isinstance(value, str):
        # Try strict JSON first
        try:
            return json.loads(value)
        except Exception:
            # Fallback: Python literal parser
            return ast.literal_eval(value)

    return value


def _encode_value(
    value: Any,
    lines: list[str],
    ctx: _EncoderContext,
    depth: int,
    is_root: bool = False,
    inline: bool = False,
) -> str | None:
    """Encode a value, appending to lines or returning inline string."""
    # Normalize non-JSON types
    value = _normalize_value(value)

    if value is None:
        return "null" if inline else None
    if isinstance(value, bool):
        return ("true" if value else "false") if inline else None
    if isinstance(value, (int, float)):
        return _format_number(value) if inline else None
    if isinstance(value, str):
        return _quote_value(value, ctx.delimiter) if inline else None
    if isinstance(value, list):
        return _encode_array(value, lines, ctx, depth, is_root)
    if isinstance(value, dict):
        return _encode_object(value, lines, ctx, depth, is_root)

    raise ValueError(f"Unsupported type: {type(value).__name__}")


def _normalize_value(value: Any) -> JsonValue:
    """Normalize Python value to JSON-compatible type."""
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    if isinstance(value, (type(None), bool)):
        return value
    if hasattr(value, "__float__"):  # Handles numpy numbers, Decimal, etc.
        try:
            f = float(value)
            return None if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return None
    if hasattr(value, "isoformat"):  # datetime-like
        return value.isoformat()
    if callable(value) or value is ...:
        return None
    # Try to convert to string as last resort
    try:
        return str(value)
    except Exception:
        return None


def _format_number(n: int | float) -> str:
    """Format number without scientific notation."""
    if isinstance(n, float):
        if math.isnan(n) or math.isinf(n):
            return "null"
        if n == 0.0:
            return "0"
        # Avoid scientific notation
        s = repr(n)
        if "e" in s.lower():
            # Format with enough precision
            if abs(n) >= 1:
                s = f"{n:.15f}".rstrip("0").rstrip(".")
            else:
                s = f"{n:.20f}".rstrip("0").rstrip(".")
        return s
    return str(n)


def _quote_value(s: str, delimiter: Delimiter) -> str:
    """Quote string if necessary, applying minimal quoting rules."""
    if not s:
        return '""'

    needs_quote = False

    # Check for leading/trailing whitespace
    if s != s.strip():
        needs_quote = True
    # Check for delimiter
    elif delimiter in s:
        needs_quote = True
    # Check for structural characters
    elif any(c in s for c in (":", "\n", "\r", '"', "\\")):
        needs_quote = True
    # Check for tab (always needs quoting outside tab mode)
    elif "\t" in s and delimiter != "\t":
        needs_quote = True
    # Check for pipe in pipe mode
    elif "|" in s and delimiter == "|":
        needs_quote = True
    # Check for reserved literals
    elif s.lower() in RESERVED_LITERALS:
        needs_quote = True
    # Check if looks like number
    elif NUMBER_PATTERN.match(s) or s.lstrip("-").replace(".", "").isdigit():
        needs_quote = True
    # Check if starts with list marker
    elif s.startswith("- ") or s == "-":
        needs_quote = True
    # Check if looks like array header
    elif re.match(r'^\[[\d#]', s) or re.match(r'^\{[^}]*\}', s):
        needs_quote = True

    if not needs_quote:
        return s

    # Escape and quote
    escaped = "".join(ESCAPE_MAP.get(c, c) for c in s)
    return f'"{escaped}"'


def _quote_key(key: str) -> str:
    """Quote object key if not a valid identifier."""
    if not key:
        return '""'
    if IDENTIFIER_PATTERN.match(key):
        return key
    escaped = "".join(ESCAPE_MAP.get(c, c) for c in key)
    return f'"{escaped}"'


def _encode_object(
    obj: dict[str, Any],
    lines: list[str],
    ctx: _EncoderContext,
    depth: int,
    is_root: bool = False,
) -> str | None:
    """Encode object as key-value pairs with indentation."""
    ctx.check_circular(obj)
    try:
        if not obj:
            # Empty object at root produces nothing; nested shows just the key
            return None

        prefix = ctx.indent * depth

        for key, val in obj.items():
            quoted_key = _quote_key(str(key))
            val = _normalize_value(val)

            if val is None:
                lines.append(f"{prefix}{quoted_key}: null")
            elif isinstance(val, bool):
                lines.append(f"{prefix}{quoted_key}: {'true' if val else 'false'}")
            elif isinstance(val, (int, float)):
                lines.append(f"{prefix}{quoted_key}: {_format_number(val)}")
            elif isinstance(val, str):
                lines.append(f"{prefix}{quoted_key}: {_quote_value(val, ctx.delimiter)}")
            elif isinstance(val, list):
                _encode_array_field(quoted_key, val, lines, ctx, depth)
            elif isinstance(val, dict):
                if not val:
                    lines.append(f"{prefix}{quoted_key}:")
                else:
                    lines.append(f"{prefix}{quoted_key}:")
                    _encode_object(val, lines, ctx, depth + 1)

        return None
    finally:
        ctx.uncheck(obj)


def _encode_array(
    arr: list[Any],
    lines: list[str],
    ctx: _EncoderContext,
    depth: int,
    is_root: bool = False,
) -> str | None:
    """Encode array, selecting optimal format."""
    ctx.check_circular(arr)
    try:
        prefix = ctx.indent * depth
        length = len(arr)
        delim_marker = _get_delimiter_marker(ctx.delimiter)
        len_str = f"[{ctx.length_marker}{length}{delim_marker}]"

        if length == 0:
            lines.append(f"{prefix}{len_str}:")
            return None

        # Normalize all items
        items = [_normalize_value(item) for item in arr]

        # Check if all primitives (inline format)
        if all(_is_primitive(item) for item in items):
            formatted = ctx.delimiter.join(_format_primitive(i, ctx.delimiter) for i in items)
            lines.append(f"{prefix}{len_str}: {formatted}")
            return None

        # Check if eligible for tabular format
        tabular_info = _check_tabular_eligible(items)
        if tabular_info:
            fields, rows = tabular_info
            fields_str = ctx.delimiter.join(_quote_key(f) for f in fields)
            lines.append(f"{prefix}{len_str}{{{fields_str}}}:")
            for row in rows:
                row_str = ctx.delimiter.join(_format_primitive(v, ctx.delimiter) for v in row)
                lines.append(f"{prefix}{ctx.indent}{row_str}")
            return None

        # Fall back to list format
        lines.append(f"{prefix}{len_str}:")
        for item in items:
            _encode_list_item(item, lines, ctx, depth)

        return None
    finally:
        ctx.uncheck(arr)


def _encode_array_field(
    key: str,
    arr: list[Any],
    lines: list[str],
    ctx: _EncoderContext,
    depth: int,
) -> None:
    """Encode array as object field with key prefix."""
    ctx.check_circular(arr)
    try:
        prefix = ctx.indent * depth
        length = len(arr)
        delim_marker = _get_delimiter_marker(ctx.delimiter)
        len_str = f"[{ctx.length_marker}{length}{delim_marker}]"

        if length == 0:
            lines.append(f"{prefix}{key}{len_str}:")
            return

        # Normalize all items
        items = [_normalize_value(item) for item in arr]

        # Check if all primitives (inline format)
        if all(_is_primitive(item) for item in items):
            formatted = ctx.delimiter.join(_format_primitive(i, ctx.delimiter) for i in items)
            lines.append(f"{prefix}{key}{len_str}: {formatted}")
            return

        # Check if eligible for tabular format
        tabular_info = _check_tabular_eligible(items)
        if tabular_info:
            fields, rows = tabular_info
            fields_str = ctx.delimiter.join(_quote_key(f) for f in fields)
            lines.append(f"{prefix}{key}{len_str}{{{fields_str}}}:")
            for row in rows:
                row_str = ctx.delimiter.join(_format_primitive(v, ctx.delimiter) for v in row)
                lines.append(f"{prefix}{ctx.indent}{row_str}")
            return

        # Fall back to list format
        lines.append(f"{prefix}{key}{len_str}:")
        for item in items:
            _encode_list_item(item, lines, ctx, depth)
    finally:
        ctx.uncheck(arr)


def _encode_list_item(
    item: JsonValue,
    lines: list[str],
    ctx: _EncoderContext,
    depth: int,
) -> None:
    """Encode a list item with - marker."""
    prefix = ctx.indent * (depth + 1)

    if _is_primitive(item):
        lines.append(f"{prefix}- {_format_primitive(item, ctx.delimiter)}")
    elif isinstance(item, list):
        # Nested array
        sub_lines: list[str] = []
        _encode_array(item, sub_lines, ctx, 0)
        if sub_lines:
            lines.append(f"{prefix}- {sub_lines[0].lstrip()}")
            for sub in sub_lines[1:]:
                lines.append(f"{prefix}  {sub.lstrip()}")
    elif isinstance(item, dict):
        if not item:
            lines.append(f"{prefix}-")
        else:
            # First key goes on same line as marker
            items_list = list(item.items())
            first_key, first_val = items_list[0]
            first_val = _normalize_value(first_val)
            qk = _quote_key(str(first_key))

            if _is_primitive(first_val):
                lines.append(f"{prefix}- {qk}: {_format_primitive(first_val, ctx.delimiter)}")
            elif isinstance(first_val, dict):
                lines.append(f"{prefix}- {qk}:")
                _encode_object(first_val, lines, ctx, depth + 2)
            elif isinstance(first_val, list):
                # Complex: encode array as field
                temp_lines: list[str] = []
                _encode_array_field(qk, first_val, temp_lines, ctx, 0)
                if temp_lines:
                    lines.append(f"{prefix}- {temp_lines[0].lstrip()}")
                    for tl in temp_lines[1:]:
                        lines.append(f"{prefix}  {tl.lstrip()}")

            # Remaining keys
            for k, v in items_list[1:]:
                v = _normalize_value(v)
                qk = _quote_key(str(k))
                inner_prefix = ctx.indent * (depth + 2)

                if _is_primitive(v):
                    lines.append(f"{inner_prefix}{qk}: {_format_primitive(v, ctx.delimiter)}")
                elif isinstance(v, dict):
                    if not v:
                        lines.append(f"{inner_prefix}{qk}:")
                    else:
                        lines.append(f"{inner_prefix}{qk}:")
                        _encode_object(v, lines, ctx, depth + 3)
                elif isinstance(v, list):
                    _encode_array_field(qk, v, lines, ctx, depth + 2)


def _get_delimiter_marker(delimiter: Delimiter) -> str:
    """Get the delimiter marker for array headers."""
    if delimiter == "\t":
        return "\t"
    if delimiter == "|":
        return "|"
    return ""


def _is_primitive(value: Any) -> bool:
    """Check if value is a JSON primitive."""
    return value is None or isinstance(value, (bool, int, float, str))


def _format_primitive(value: Any, delimiter: Delimiter) -> str:
    """Format a primitive value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, str):
        return _quote_value(value, delimiter)
    return "null"


def _check_tabular_eligible(
    items: list[JsonValue],
) -> tuple[list[str], list[list[Any]]] | None:
    """
    Check if array is eligible for tabular format.
    Returns (field_names, rows) if eligible, None otherwise.

    Eligibility requires:
    - All items are non-empty dicts
    - All dicts have identical keys (same names, same order)
    - All values are primitives
    """
    if not items:
        return None

    # All must be non-empty dicts
    if not all(isinstance(item, dict) and item for item in items):
        return None

    first = items[0]
    assert isinstance(first, dict)  # for type checker
    fields = list(first.keys())

    rows: list[list[Any]] = []
    for item in items:
        assert isinstance(item, dict)  # for type checker
        # Check same keys in same order
        if list(item.keys()) != fields:
            return None
        # Check all values are primitives
        values = list(item.values())
        if not all(_is_primitive(v) for v in values):
            return None
        rows.append(values)

    return fields, rows


# Convenience aliases
encode = json_to_toon

