"""IST datetime parsing for Trisul report time windows."""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))

_IST_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %I:%M:%S %p",
    "%Y-%m-%d %I:%M %p",
    "%Y-%m-%d %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %I:%M:%S %p",
    "%d-%m-%Y %H:%M",
)

_TIME_RANGE_RE = re.compile(
    r"\bfrom\s+"
    r"(\d{4}-\d{2}-\d{2}\s+"
    r"(?:\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?"
    r"|\d{1,2}\s*(?:am|pm)))"
    r"\s+to\s+"
    r"(\d{4}-\d{2}-\d{2}\s+"
    r"(?:\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?"
    r"|\d{1,2}\s*(?:am|pm)))",
    re.IGNORECASE,
)


def normalize_datetime_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return re.sub(r"\b(am|pm)\b", lambda m: m.group(1).upper(), text, flags=re.IGNORECASE)


def parse_ist_datetime(text: str) -> int:
    """Parse a human-readable datetime string as IST; return epoch seconds."""
    normalized = normalize_datetime_text(text)
    if not normalized:
        raise ValueError("empty datetime string")
    for fmt in _IST_DATETIME_FORMATS:
        try:
            dt = datetime.strptime(normalized, fmt).replace(tzinfo=IST)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise ValueError(f"Could not parse IST datetime: {text!r}")


def format_ist_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), IST).strftime("%Y-%m-%d %H:%M:%S %z IST")


def extract_time_range_from_query(query: str) -> Optional[Tuple[str, str]]:
    match = _TIME_RANGE_RE.search(query or "")
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def resolve_absolute_time_window(
    start_ts=None,
    end_ts=None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Resolve an absolute report window.

    Human-readable start_time/end_time (IST) take precedence over epoch
    start_ts/end_ts supplied by the LLM.
    """
    if start_time and end_time:
        from_ts = parse_ist_datetime(start_time)
        to_ts = parse_ist_datetime(end_time)
        logging.info(
            "[time_utils] parsed start_time=%r -> %s, end_time=%r -> %s",
            start_time,
            format_ist_epoch(from_ts),
            end_time,
            format_ist_epoch(to_ts),
        )
        if from_ts >= to_ts:
            raise ValueError(
                f"start_time must be before end_time ({start_time!r} >= {end_time!r})"
            )
        return from_ts, to_ts
    if start_ts is not None and end_ts is not None:
        return int(start_ts), int(end_ts)
    return None, None
