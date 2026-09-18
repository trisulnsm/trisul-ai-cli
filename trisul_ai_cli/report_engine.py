"""
Server-side dynamic report builder (Excel + PDF).

Fetches Trisul data via TRP and assembles rows without LLM involvement.
Supports: key traffic time-series, top-N multi-meter, key aggregates, key monitor layout.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# User-facing data types returned in verification metadata
DATA_TYPE_KEY_TRAFFIC = "key_traffic"
DATA_TYPE_TOPPER = "topper"
DATA_TYPE_KEY_SUMMARY = "key_summary"
DATA_TYPE_KEY_MONITOR = "key_monitor"


def _lazy_server():
    from trisul_ai_cli import server as s
    return s


def _clean_key_label(label: str) -> str:
    if not label:
        return ""
    text = str(label).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').strip()


def _meter_row_key(minfo: dict) -> str:
    """Unique row/column key per meter (avoids collapsing Into/Outof with Recv/Xmit)."""
    mid = int(minfo.get("id", 0))
    desc = (minfo.get("description") or minfo.get("name") or f"meter_{mid}").strip()
    slug = re.sub(r"[^a-z0-9]+", "_", desc.lower()).strip("_")
    return slug or f"meter_{mid}"


def _is_pct_meter(minfo: dict) -> bool:
    units = (minfo.get("units") or "").lower()
    return units in ("pct", "percent", "%")


def _infer_util_meter_refs(
    columns: Optional[List[dict]],
    computed_columns: Optional[List[dict]],
) -> Optional[List[str]]:
    """Detect FlowIntfs utilization columns and return Recv/Xmit-Util meter names."""
    refs: set = set()
    for col in (columns or []) + (computed_columns or []):
        for ref in (col.get("sum_of_meters") or col.get("sum_of") or []):
            refs.add(str(ref).strip())
        for field in ("key", "header"):
            val = str(col.get(field) or "").strip()
            if val and "util" in val.lower():
                refs.add(val)
    if not refs:
        return None
    if any(
        "util" in r.lower()
        or re.sub(r"[^a-z0-9]+", "", r.lower()) in {"recvutil", "xmitutil", "totalutilization"}
        for r in refs
    ):
        return ["Recv-Util", "Xmit-Util"]
    return None


def _topper_gauge_value(keyt) -> int:
    """Gauge display value from topper KeyT (matches webtrisul metric_avg)."""
    avg = int(getattr(keyt, "metric_avg", 0) or 0)
    if avg:
        return avg
    return int(getattr(keyt, "metric", 0) or 0)


def _index_topper_avg(bucket: dict, keyt, val: int) -> None:
    bucket[keyt.key] = val
    if keyt.readable:
        bucket[keyt.readable] = val
    label = (keyt.label or "").strip()
    if label:
        bucket[label] = val


def _lookup_topper_avg(bucket: dict, entry: dict) -> Optional[int]:
    if not bucket:
        return None
    for k in (
        entry.get("internal_key"),
        entry.get("lookup"),
        entry.get("readable"),
        entry.get("label"),
    ):
        if k and k in bucket:
            return bucket[k]
    return None


def _snmp_ifspeed_bps(attrs: dict, direction: str) -> int:
    """Match webtrisul utils.get_snmp_ifspeed (rx/tx/in/out)."""
    direction = (direction or "").lower()
    if direction in ("out", "tx"):
        suffix = "tx"
    else:
        suffix = "rx"
    for key in (f"snmp.ifspeed_{suffix}", "snmp.ifspeed"):
        raw = attrs.get(key)
        if raw is None or raw == "":
            continue
        try:
            speed = int(float(raw))
            if speed > 0:
                return speed
        except (TypeError, ValueError):
            continue
    return 0


def _crop_latest_window(from_ts: int, to_ts: int, bucket_secs: int) -> tuple:
    """Match webtrisul TimeInterval.crop_latest (last N seconds of window)."""
    to_ts = int(to_ts)
    from_ts = int(from_ts)
    crop_from = max(from_ts, to_ts - max(int(bucket_secs), 1))
    return crop_from, to_ts


def _util_pct_from_lastbwin(lastbwin_bytes: int, ifspeed_bps: int, bucket_secs: int) -> int:
    """
    Match router_intf_drill.js:
      Math.round(((lastbwin * 8 / bucket_secs) / ifspeed) * 100)
  where lastbwin = v.last.val * topn_bucket_size from ax_get_retro_latest_toppers.
    """
    if ifspeed_bps <= 0:
        return -1
    bps = (int(lastbwin_bytes or 0) * 8) / max(int(bucket_secs), 1)
    return int(round((bps / ifspeed_bps) * 100))


def _webtrisul_util_pct(latest_bps: int, ifspeed_bps: int) -> int:
    """Utilization from a rate already in bits/sec (COUNTER_ITEM_NG latests fallback)."""
    if ifspeed_bps <= 0:
        return -1
    return int(round((int(latest_bps or 0) / ifspeed_bps) * 100))


def _router_key_from_entry(entry: dict) -> str:
    """FlowIntfs router TRP key (hex prefix before '_')."""
    router_key = entry.get("router_key")
    if router_key:
        return str(router_key)
    for field in ("internal_key", "lookup", "readable"):
        val = entry.get(field)
        if val and "_" in str(val):
            return str(val).split("_", 1)[0]
    return ""


def _last_positive_trend_rate(meter_values) -> int:
    """Last positive Bps rate from a topper-trend meter series."""
    if meter_values is None or not getattr(meter_values, "values", None):
        return 0
    positives = [
        int(v.val) for v in meter_values.values if int(getattr(v, "val", 0) or 0) > 0
    ]
    return positives[-1] if positives else 0


def _fetch_router_retro_latest_bw_map(
    get_response,
    trp_pb2,
    zmq_endpoint,
    counter_group_guid,
    router_key: str,
    meter_id: int,
    from_ts,
    to_ts,
    topper_bucket_secs: int,
    maxitems: int = 500,
) -> dict:
    """
    Match webtrisul ax_get_retro_latest_toppers for one router drill-down.

    router_intf_drill.js calls get_interface_list(router_key, meter, field, -1) which
  posts key_filter=router_key to ax_get_retro_latest_toppers (not per-interface keys).
    """
    if not router_key:
        return {}
    crop_from, crop_to = _crop_latest_window(from_ts, to_ts, topper_bucket_secs)
    req = trp_pb2.Message()
    req.trp_command = req.TOPPER_TREND_REQUEST
    q = req.topper_trend_request
    q.counter_group = counter_group_guid
    q.meter = int(meter_id)
    q.maxitems = int(maxitems)
    q.key_filter = str(router_key).strip()
    getattr(q.time_interval, "from").tv_sec = crop_from
    q.time_interval.to.tv_sec = crop_to
    try:
        msg = get_response(zmq_endpoint, req)
    except Exception as ex:
        logging.warning(
            f"[report_engine] router retro trend failed router={router_key!r} "
            f"meter={meter_id}: {ex}"
        )
        return {}
    bw_map: dict = {}
    for kt in list(getattr(msg, "keytrends", None) or []):
        if not kt.meters:
            continue
        rate_bps = _last_positive_trend_rate(kt.meters[0])
        if rate_bps <= 0:
            continue
        intf_key = kt.key.key if kt.key and kt.key.key else ""
        if intf_key:
            bw_map[intf_key] = rate_bps * int(topper_bucket_secs)
    logging.info(
        f"[report_engine] router retro trend router={router_key!r} meter={meter_id} "
        f"interfaces={len(bw_map)} crop=[{crop_from},{crop_to}]"
    )
    return bw_map


def _lookup_router_lastbwin(bw_map: dict, entry: dict) -> int:
    for k in (entry.get("internal_key"), entry.get("lookup"), entry.get("readable")):
        if k and k in bw_map:
            return bw_map[k]
    return 0


def _enrich_flowintf_snmp_attrs(
    get_response,
    trp_pb2,
    zmq_endpoint,
    counter_group_guid,
    key_entries: List[dict],
) -> None:
    """Merge SNMP attrs (ifspeed_rx/tx) like webtrisul ax_get_interface_attributes."""
    keys: List[str] = []
    for entry in key_entries:
        ik = entry.get("internal_key")
        if ik and ik not in keys:
            keys.append(ik)
    if not keys:
        return
    req = trp_pb2.Message()
    req.trp_command = req.SEARCH_KEYS_REQUEST
    q = req.search_keys_request
    q.counter_group = counter_group_guid
    q.maxitems = len(keys)
    q.get_attributes = True
    q.keys.extend(keys)
    try:
        resp = get_response(zmq_endpoint, req)
    except Exception as ex:
        logging.warning(f"[report_engine] FlowIntfs SNMP attr fetch failed: {ex}")
        return
    attr_by_key: dict = {}
    for keyt in list(getattr(resp, "keys", None) or []):
        if not keyt.key:
            continue
        attr_by_key[keyt.key] = {a.attr_name: a.attr_value for a in keyt.attributes}
    for entry in key_entries:
        merged = attr_by_key.get(entry.get("internal_key"))
        if not merged:
            continue
        attrs = dict(entry.get("attrs") or {})
        attrs.update(merged)
        entry["attrs"] = attrs


def _set_util_row_fields(row: dict, util_meters_info: dict, recv_util: int, xmit_util: int) -> None:
    """Write in/out utilization under slug and description column keys."""
    values_by_id = {}
    for mid, minfo in util_meters_info.items():
        desc = (minfo.get("description") or "").strip().lower()
        if "recv" in desc:
            values_by_id[mid] = recv_util
        elif "xmit" in desc:
            values_by_id[mid] = xmit_util
    ordered = list(util_meters_info.keys())
    if not values_by_id and len(ordered) >= 2:
        values_by_id[ordered[0]] = recv_util
        values_by_id[ordered[1]] = xmit_util
    for mid, val in values_by_id.items():
        minfo = util_meters_info[mid]
        col = _meter_row_key(minfo)
        row[col] = val
        desc = (minfo.get("description") or "").strip()
        if desc:
            row[desc] = val


def _build_flowintf_util_rows(
    get_response,
    trp_pb2,
    get_key_meter_stats,
    counter_group_guid,
    key_entries,
    util_meters_info: dict,
    from_ts,
    to_ts,
    zmq_endpoint,
    bw_meter_ids,
    bw_meters_info,
    topper_bucket_secs: int,
) -> List[dict]:
    """
    FlowIntfs utilization rows aligned with webtrisul nflow router interface list.

    Uses retro latest Recv/Xmit (meters 1/2) + SNMP ifspeed on the last topper bucket,
    fetched per router like gen_drilldown / ax_get_retro_latest_toppers.
    """
    recv_id = bw_meter_ids[0]
    xmit_id = bw_meter_ids[1] if len(bw_meter_ids) > 1 else bw_meter_ids[0]
    crop_from, crop_to = _crop_latest_window(from_ts, to_ts, topper_bucket_secs)

    _enrich_flowintf_snmp_attrs(
        get_response, trp_pb2, zmq_endpoint, counter_group_guid, key_entries
    )

    router_keys = sorted({_router_key_from_entry(e) for e in key_entries if _router_key_from_entry(e)})
    trend_maxitems = max(500, len(key_entries) * 10)
    recv_by_router: dict = {}
    xmit_by_router: dict = {}
    for router_key in router_keys:
        recv_by_router[router_key] = _fetch_router_retro_latest_bw_map(
            get_response, trp_pb2, zmq_endpoint, counter_group_guid,
            router_key, recv_id, from_ts, to_ts, topper_bucket_secs, trend_maxitems,
        )
        xmit_by_router[router_key] = _fetch_router_retro_latest_bw_map(
            get_response, trp_pb2, zmq_endpoint, counter_group_guid,
            router_key, xmit_id, from_ts, to_ts, topper_bucket_secs, trend_maxitems,
        )

    rows: List[dict] = []
    for entry in key_entries:
        lookup = entry["lookup"]
        attrs = entry.get("attrs") or {}
        router_key = _router_key_from_entry(entry)
        lastbwin = _lookup_router_lastbwin(recv_by_router.get(router_key, {}), entry)
        lastbwout = _lookup_router_lastbwin(xmit_by_router.get(router_key, {}), entry)

        ifspeed_rx = _snmp_ifspeed_bps(attrs, "rx")
        ifspeed_tx = _snmp_ifspeed_bps(attrs, "tx")
        if lastbwin > 0:
            in_util = _util_pct_from_lastbwin(lastbwin, ifspeed_rx, topper_bucket_secs)
        else:
            in_util = -1
        if lastbwout > 0:
            out_util = _util_pct_from_lastbwin(lastbwout, ifspeed_tx, topper_bucket_secs)
        else:
            out_util = -1

        stats = None
        if in_util < 0 or out_util < 0:
            try:
                stats = get_key_meter_stats(
                    counter_group_guid, lookup, bw_meter_ids,
                    crop_from, crop_to, zmq_endpoint, bw_meters_info,
                )
            except Exception as ex:
                logging.warning(f"[report_engine] bw stats fallback failed for '{lookup}': {ex}")
                continue
            if in_util < 0:
                in_util = _webtrisul_util_pct(
                    stats["meters"][recv_id]["latests"], ifspeed_rx
                )
            if out_util < 0:
                out_util = _webtrisul_util_pct(
                    stats["meters"][xmit_id]["latests"], ifspeed_tx
                )

        row = {
            "key": entry.get("readable") or (stats.get("key") if stats else None) or lookup,
            "name": entry.get("label") or (stats.get("key") if stats else None) or lookup,
            "description": entry.get("description") or entry.get("interface_description") or "",
            "readable": entry.get("readable") or entry.get("router_ip") or entry.get("key") or lookup,
            "label": entry.get("label") or entry.get("interface") or entry.get("name") or lookup,
            "router_ip": entry.get("router_ip"),
            "router_name": entry.get("router_name"),
            "interface": entry.get("interface"),
            "interface_description": entry.get("interface_description") or entry.get("description") or "",
            "_sort_total": in_util if in_util >= 0 else 0,
        }
        _set_util_row_fields(row, util_meters_info, in_util, out_util)
        rows.append(row)
    return rows


def _meter_cell_format(minfo: dict, *, timeseries: bool = False) -> str:
    units = (minfo.get("units") or "").lower()
    if _is_pct_meter(minfo):
        return "percent"
    if units.endswith("ps") or units == "bps":
        return "bandwidth" if timeseries else "volume"
    if units in ("flw", "flows", "conn", "conns", "pkts", "packets", "alts", "alerts"):
        return "number"
    return "number"


def _bucket_size_secs(cg_group: dict) -> int:
    raw = cg_group.get("bucketSize") or cg_group.get("bucket_size") or "60000"
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        return 60
    return max(ms // 1000, 1)


def _topper_bucket_secs(cg_group: dict) -> int:
    raw = cg_group.get("topperBucketSize") or cg_group.get("topper_bucket_size") or "300"
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 300


def _key_lookup_label(entry: dict, fallback: str = "") -> str:
    """Best single TRP key.label guess (prefer _key_lookup_candidates for fetch)."""
    candidates = _key_lookup_candidates(entry, fallback)
    return candidates[0] if candidates else str(fallback).strip().lower()


def _key_lookup_candidates(entry: dict, user_input: str = "") -> List[str]:
    """Ordered TRP key.label candidates (https→label; ASN 15169→readable)."""
    user_input = str(user_input or "").strip().lower()
    readable = str(entry.get("readable") or "").strip().lower()
    label = str(entry.get("label") or "").strip().lower()
    lookup = str(entry.get("lookup") or "").strip().lower()

    candidates: List[str] = []

    def _add(val: str) -> None:
        v = str(val or "").strip().lower()
        if v and v not in candidates:
            candidates.append(v)

    _add(user_input)
    if readable and readable != label:
        _add(readable)
    _add(label)
    _add(lookup)
    return candidates


def _key_traffic_bucket_warning(cg_group: dict, stat_bucket_secs: int, duration_secs: int, row_count: int) -> Optional[str]:
    topper_bucket = _topper_bucket_secs(cg_group)
    expected_minute_rows = max(int(duration_secs) // 60, 1)
    if stat_bucket_secs > 60 and row_count < expected_minute_rows:
        cg_name = cg_group.get("name") or "counter group"
        if stat_bucket_secs == topper_bucket:
            return (
                f"{cg_name} key traffic is stored every {stat_bucket_secs}s (same as topper bucket). "
                f"Got {row_count} rows for {duration_secs // 60} minutes; per-minute rows need BucketSizeMS=60000."
            )
        return (
            f"{cg_name} key traffic bucket is {stat_bucket_secs}s. "
            f"Got {row_count} rows for {duration_secs // 60} minutes; expected ~{expected_minute_rows} per-minute rows."
        )
    return None


def _infer_bucket_secs_from_stats(stats, fallback: int) -> int:
    """Infer actual bucket interval from consecutive stat timestamps."""
    if not stats or len(stats) < 2:
        return fallback
    gaps = []
    prev_ts = None
    for stat in stats:
        ts = int(stat.ts_tv_sec)
        if prev_ts is not None:
            gap = ts - prev_ts
            if gap > 0:
                gaps.append(gap)
        prev_ts = ts
    if not gaps:
        return fallback
    return max(min(gaps), 1)


def _resolve_intent_and_source(
    intent: str,
    source: str,
    row_layout: str,
    keys: Optional[List[str]],
    max_count: Optional[int],
) -> Tuple[str, str, str]:
    """
    Return (intent, source, row_layout) after applying intent and auto rules.

    intent: auto | key_traffic | topper | key_summary | key_monitor
    """
    intent = (intent or "auto").lower()
    source = (source or "auto").lower()
    row_layout = (row_layout or "auto").lower()
    has_keys = bool(keys)
    has_topn = max_count is not None and int(max_count) > 0

    if intent == "key_traffic":
        if not has_keys:
            raise ValueError("intent=key_traffic requires keys (e.g. ['https']).")
        return intent, "key_timeseries", "timeseries"

    if intent == "topper":
        if not has_topn:
            max_count = 10
        return intent, "topper", "per_key" if row_layout == "auto" else row_layout

    if intent == "key_monitor":
        if not has_keys:
            raise ValueError("intent=key_monitor requires keys.")
        return intent, "key_stats", "per_key_meter"

    if intent == "key_summary":
        if not has_keys:
            raise ValueError("intent=key_summary requires keys.")
        return intent, "key_stats", "per_key"

    # intent == auto
    if source == "key_timeseries" or row_layout == "timeseries":
        return DATA_TYPE_KEY_TRAFFIC, "key_timeseries", "timeseries"
    if source == "topper" or (has_topn and not has_keys):
        return DATA_TYPE_TOPPER, "topper", "per_key" if row_layout in ("auto", "per_key") else row_layout
    if source == "key_stats":
        layout = "per_key_meter" if row_layout == "per_key_meter" else "per_key"
        return DATA_TYPE_KEY_SUMMARY if layout == "per_key" else DATA_TYPE_KEY_MONITOR, "key_stats", layout

    if has_keys and not has_topn:
        # Default: explicit keys without top-N => key traffic time-series (NOT aggregate/topper-like)
        return DATA_TYPE_KEY_TRAFFIC, "key_timeseries", "timeseries"
    if has_topn and not has_keys:
        return DATA_TYPE_TOPPER, "topper", "per_key"
    if has_keys and has_topn:
        return DATA_TYPE_TOPPER, "topper", "per_key"

    raise ValueError(
        "Cannot infer report type: provide keys (key traffic) or max_count (top-N topper)."
    )


def _search_resolve_key(get_response, trp_pb2, zmq_endpoint, counter_group_guid, key_input: str) -> dict:
    """Resolve alias (https) to Trisul key via SEARCH_KEYS."""
    key_input = str(key_input).strip()
    lookup = key_input.lower()

    def _search(label=None, pattern=None):
        req = trp_pb2.Message()
        req.trp_command = req.SEARCH_KEYS_REQUEST
        q = req.search_keys_request
        q.counter_group = counter_group_guid
        q.maxitems = 5
        if label:
            q.label = label
        if pattern:
            q.pattern = pattern
        resp = get_response(zmq_endpoint, req)
        return list(resp.keys)

    found = _search(label=lookup)
    if not found:
        found = _search(pattern=lookup)
    if not found:
        found = _search(pattern=f"(?i).*{re.escape(key_input)}.*")

    if found:
        k = found[0]
        label = _clean_key_label(k.label) if k.label else (k.readable or key_input)
        return {
            "lookup": key_input.strip().lower(),
            "readable": k.readable or k.key,
            "label": label,
            "description": k.description or "",
        }
    return {"lookup": lookup, "readable": key_input, "label": key_input, "description": ""}


def _fetch_cg_topper(
    get_response, trp_pb2, zmq_endpoint, counter_group_guid, meter, max_count,
    from_ts, to_ts, is_system_key, is_interface_key, flowintfs_guid,
) -> list:
    req = trp_pb2.Message()
    req.trp_command = req.COUNTER_GROUP_TOPPER_REQUEST
    topper = req.counter_group_topper_request
    topper.counter_group = counter_group_guid
    topper.meter = int(meter)
    topper.maxitems = int(max_count)
    topper.get_key_attributes = True
    if counter_group_guid.upper() == flowintfs_guid.upper():
        topper.inverse_key_filter = "SYS:GROUP"
    getattr(topper.time_interval, "from").tv_sec = int(from_ts)
    topper.time_interval.to.tv_sec = int(to_ts)
    resp = get_response(zmq_endpoint, req)
    keys = list(resp.keys)
    if counter_group_guid.upper() == flowintfs_guid.upper():
        keys = [k for k in keys if is_interface_key(k.key)]
    else:
        keys = [k for k in keys if not is_system_key(k.key)]
    logging.info(f"[report_engine] topper kept={len(keys)}")
    return keys


def _build_topper_metric_avgs(
    get_response,
    trp_pb2,
    zmq_endpoint,
    counter_group_guid,
    meter_ids,
    sort_meter,
    topper_keys,
    from_ts,
    to_ts,
    max_count,
    is_system_key,
    is_interface_key,
    flowintfs_guid,
) -> dict:
    """
    Build per-meter metric_avg lookups from topper responses (webtrisul-aligned).

    Reuses the initial sort_meter topper_keys; fetches additional meters only when needed.
    """
    avgs_by_meter: dict = {}
    sort_avgs: dict = {}
    for keyt in topper_keys:
        _index_topper_avg(sort_avgs, keyt, _topper_gauge_value(keyt))
    avgs_by_meter[int(sort_meter)] = sort_avgs
    logging.info(
        f"[report_engine] topper metric_avg meter={sort_meter} keys={len(sort_avgs)}"
    )

    fetch_count = min(max(int(max_count or 10) * 3, 500), 2000)
    for mid in meter_ids:
        if int(mid) == int(sort_meter):
            continue
        keys = _fetch_cg_topper(
            get_response,
            trp_pb2,
            zmq_endpoint,
            counter_group_guid,
            mid,
            fetch_count,
            from_ts,
            to_ts,
            is_system_key,
            is_interface_key,
            flowintfs_guid,
        )
        meter_avgs: dict = {}
        for keyt in keys:
            _index_topper_avg(meter_avgs, keyt, _topper_gauge_value(keyt))
        avgs_by_meter[int(mid)] = meter_avgs
        logging.info(
            f"[report_engine] topper metric_avg meter={mid} keys={len(meter_avgs)}"
        )
    return avgs_by_meter


def _fetch_key_timeseries(get_response, trp_pb2, zmq_endpoint, counter_group_guid, entry, from_ts, to_ts, user_input=""):
    """Fetch per-key time series; try label/readable candidates until stats are returned."""
    candidates = _key_lookup_candidates(entry, user_input)
    last_resp = None
    for candidate in candidates:
        req = trp_pb2.Message()
        req.trp_command = req.COUNTER_ITEM_NG_REQUEST
        ng = req.counter_item_ng_request
        ng.counter_group = counter_group_guid
        ng.key.label = candidate
        ng.get_key_attributes = True
        getattr(ng.time_interval, "from").tv_sec = int(from_ts)
        ng.time_interval.to.tv_sec = int(to_ts)
        resp = get_response(zmq_endpoint, req)
        last_resp = resp
        if resp.stats:
            if candidate != (candidates[0] if candidates else candidate):
                logging.info(f"[report_engine] key timeseries resolved via label={candidate!r}")
            return resp
    return last_resp


def _timeseries_value(raw, minfo: dict) -> int:
    """Match WebTrisul key-traffic charts: Bps meters show bits/sec (raw × 8)."""
    val = int(raw or 0)
    if (minfo.get("units") or "").lower() == "bps":
        return val * 8
    return val


def _build_timeseries_rows(resp, key_meta: dict, meter_ids, meters_info, bucket_secs) -> List[dict]:
    readable = key_meta.get("readable") or (
        resp.key.readable if resp.HasField("key") and resp.key.readable else key_meta.get("lookup")
    )
    label = key_meta.get("label") or _clean_key_label(resp.key.label if resp.HasField("key") else "")
    description = key_meta.get("description") or (
        resp.key.description if resp.HasField("key") and resp.key.description else ""
    )

    rows = []
    for stat in resp.stats:
        ts = int(stat.ts_tv_sec)
        row = {
            "timestamp": ts,
            "key": readable,
            "name": label,
            "description": description,
        }
        for mid in meter_ids:
            col = _meter_row_key(meters_info[mid])
            raw = stat.values[mid] if mid < len(stat.values) else 0
            row[col] = _timeseries_value(raw, meters_info[mid])
        rows.append(row)
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def _build_aggregate_rows(
    get_key_meter_stats,
    counter_group_guid,
    key_entries,
    meter_ids,
    meters_info,
    from_ts,
    to_ts,
    zmq_endpoint,
    topper_metric_avgs=None,
) -> List[dict]:
    rows = []
    use_topper_avgs = topper_metric_avgs is not None
    for entry in key_entries:
        lookup = entry["lookup"]
        internal_key = entry.get("internal_key") or lookup
        stats = None
        need_stats = not use_topper_avgs or any(
            not _is_pct_meter(meters_info[mid])
            or _lookup_topper_avg(topper_metric_avgs.get(mid, {}), entry) is None
            for mid in meter_ids
        )
        if need_stats:
            try:
                stats = get_key_meter_stats(
                    counter_group_guid, lookup, meter_ids, from_ts, to_ts, zmq_endpoint, meters_info
                )
            except Exception as ex:
                if not use_topper_avgs:
                    logging.warning(f"[report_engine] stats failed for '{lookup}': {ex}")
                    continue
                logging.warning(
                    f"[report_engine] stats fallback for '{lookup}' after topper avgs: {ex}"
                )
        row = {
            "key": entry.get("readable") or (stats.get("key") if stats else None) or lookup,
            "name": entry.get("label") or (stats.get("key") if stats else None) or lookup,
            "description": entry.get("description") or entry.get("interface_description") or "",
            "readable": entry.get("readable") or entry.get("router_ip") or entry.get("key") or lookup,
            "label": entry.get("label") or entry.get("interface") or entry.get("name") or lookup,
            "router_ip": entry.get("router_ip"),
            "router_name": entry.get("router_name"),
            "interface": entry.get("interface"),
            "interface_description": entry.get("interface_description") or entry.get("description") or "",
            "_sort_total": 0,
        }
        for i, mid in enumerate(meter_ids):
            col = _meter_row_key(meters_info[mid])
            if use_topper_avgs and _is_pct_meter(meters_info[mid]):
                val = _lookup_topper_avg(topper_metric_avgs.get(mid, {}), entry)
                if val is None and stats:
                    val = stats["meters"][mid]["averages"]
                row[col] = val if val is not None else -1
                desc = (meters_info[mid].get("description") or "").strip()
                if desc:
                    row[desc] = row[col]
            elif stats:
                meter_stats = stats["meters"][mid]
                if _is_pct_meter(meters_info[mid]):
                    row[col] = meter_stats["averages"]
                else:
                    row[col] = meter_stats["totals"]
            else:
                row[col] = -1
            if i == 0:
                row["_sort_total"] = row[col] or 0
        rows.append(row)
    return rows


def _build_per_key_meter_rows(get_key_meter_stats, fmt_volume, fmt_bw, counter_group_guid, key_entries,
                              meter_ids, meters_info, from_ts, to_ts, zmq_endpoint) -> List[dict]:
    flat_rows = []
    for entry in key_entries:
        lookup = entry["lookup"]
        try:
            stats = get_key_meter_stats(
                counter_group_guid, lookup, meter_ids, from_ts, to_ts, zmq_endpoint, meters_info
            )
        except Exception as ex:
            logging.warning(f"[report_engine] key meter failed for '{lookup}': {ex}")
            continue
        for i, mid in enumerate(meter_ids):
            m = stats["meters"][mid]
            flat_rows.append({
                "name": stats["key"] if i == 0 else "",
                "meter": m["name"],
                "total": fmt_volume(m["totals"]),
                "max": fmt_bw(m["maximums"], m["units"]),
                "min": fmt_bw(m["minimums"], m["units"]),
                "avg": fmt_bw(m["averages"], m["units"]),
                "latest": fmt_bw(m["latests"], m["units"]),
            })
    return flat_rows


def _flowintf_auto_columns(resolved_meters: List[dict]) -> List[dict]:
    """Default FlowIntfs columns: identity fields, then meters in request order."""
    columns = [
        {"header": "Router IP", "key": "router_ip", "format": "text"},
        {"header": "Router Name", "key": "router_name", "format": "text"},
        {"header": "Interface", "key": "interface", "format": "text"},
        {"header": "Interface Description", "key": "interface_description", "format": "text"},
    ]
    seen: set = set()
    for m in resolved_meters:
        col_key = _meter_row_key(m)
        if col_key in seen:
            continue
        seen.add(col_key)
        header = (m.get("description") or m.get("name") or col_key).strip()
        columns.append({
            "header": header,
            "key": col_key,
            "format": _meter_cell_format(m),
            "format_args": {"units": m.get("units", "bps")},
        })
    return columns


def _auto_columns(row_layout, resolved_meters, data_type) -> List[dict]:
    columns = []
    if row_layout == "timeseries":
        columns.append({"header": "Time (IST)", "key": "timestamp", "format": "datetime_epoch"})
        columns.append({"header": "Key", "key": "key", "format": "text"})
    elif data_type != DATA_TYPE_KEY_MONITOR:
        columns.extend([
            {"header": "Key", "key": "key", "format": "text"},
            {"header": "Name", "key": "name", "format": "text"},
            {"header": "Description", "key": "description", "format": "text"},
        ])

    if data_type == DATA_TYPE_KEY_MONITOR:
        return [
            {"header": "Name", "key": "name"},
            {"header": "Meter", "key": "meter"},
            {"header": "Total", "key": "total"},
            {"header": "Max", "key": "max"},
            {"header": "Min", "key": "min"},
            {"header": "Avg", "key": "avg"},
            {"header": "Latest", "key": "latest"},
        ]

    seen = set()
    for m in resolved_meters:
        col_key = _meter_row_key(m)
        if col_key in seen:
            continue
        seen.add(col_key)
        header = (m.get("description") or m.get("name") or col_key).strip()
        columns.append({
            "header": header,
            "key": col_key,
            "format": _meter_cell_format(m, timeseries=(row_layout == "timeseries")),
            "format_args": {"units": m.get("units", "bps")},
        })
    return columns


def _verify_report(data_type, source, rows, keys, max_count, from_ts, to_ts,
                   stat_bucket_secs=None, duration_secs=None, cg_group=None,
                   columns=None, resolved_meters=None) -> dict:
    """Post-fetch sanity checks; returns verification metadata."""
    issues = []
    if not rows:
        issues.append("empty_rows")

    if data_type == DATA_TYPE_KEY_TRAFFIC:
        if source != "key_timeseries":
            issues.append(f"key_traffic must use key_timeseries, got {source}")
        if max_count:
            issues.append("key_traffic must not use max_count (that is topper mode)")
        ts_vals = [r.get("timestamp") for r in rows if r.get("timestamp")]
        if ts_vals and ts_vals != sorted(ts_vals):
            issues.append("timestamps_not_sorted")
        keys_in_rows = {r.get("key") for r in rows if r.get("key")}
        if keys and len(keys_in_rows) > len(keys) + 2:
            issues.append("too_many_distinct_keys_for_key_traffic")
        if stat_bucket_secs and duration_secs:
            expected_minute_rows = max(int(duration_secs) // 60, 1)
            if stat_bucket_secs > 60 and len(rows) < expected_minute_rows:
                issues.append(
                    f"coarse_key_traffic_bucket:{stat_bucket_secs}s:"
                    f"{len(rows)}_rows_for_{duration_secs // 60}min"
                )

    if data_type == DATA_TYPE_TOPPER:
        if source != "topper":
            issues.append(f"topper must use source=topper, got {source}")
        if len(rows) > int(max_count or 10) + 1:
            issues.append("row_count_exceeds_max_count")

    if columns and rows:
        row_keys = set()
        for row in rows:
            if isinstance(row, dict):
                row_keys.update(row.keys())
        empty_cols = []
        headers = []
        keys = []
        for col in columns:
            key = col.get("key") or col.get("header")
            header = (col.get("header") or "").strip()
            if header:
                headers.append(header.lower())
            if col.get("key"):
                keys.append(str(col.get("key")).strip().lower())
            resolved = _resolve_column_key(
                key, resolved_meters or [], row_keys, header=col.get("header") or "",
            )
            if resolved not in row_keys:
                empty_cols.append(key)
            elif all(_row_field_value(row, resolved) in (None, "") for row in rows):
                empty_cols.append(key)
        if empty_cols:
            issues.append(f"empty_column_data:{','.join(empty_cols[:5])}")
        if len(headers) != len(set(headers)):
            issues.append("duplicate_column_headers")

    return {
        "verified": len(issues) == 0,
        "data_type": data_type,
        "source": source,
        "row_count": len(rows),
        "issues": issues,
    }


_DATETIME_STRING_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}")
_LLM_TRAFFIC_ROW_KEYS = frozenset({
    "upload", "download", "total",
    "upload_bytes", "download_bytes", "total_bytes",
})
_COMMON_BUCKET_SECS = (60, 300, 600)


def _row_has_string_datetime(row: dict) -> bool:
    for key in ("time", "timestamp", "Time (IST)"):
        val = row.get(key)
        if isinstance(val, str) and _DATETIME_STRING_RE.search(val.strip()):
            return True
    return False


def _looks_like_bucket_scaled_volume(val, bucket_secs: int = 60) -> bool:
    """True when val looks like raw Trisul counter × bucket seconds (not bits/sec)."""
    if not isinstance(val, (int, float)) or val <= 0:
        return False
    for bucket in _COMMON_BUCKET_SECS:
        implied_raw = val / bucket
        implied_bps = implied_raw * 8
        if 1_000 <= implied_bps <= 1_000_000_000_000 and abs(implied_raw - round(implied_raw)) < 1:
            return True
    return False


def _columns_use_volume_for_direction(columns: Optional[List[dict]]) -> bool:
    if not columns:
        return False
    for col in columns:
        key = (col.get("key") or "").lower()
        if col.get("format") == "volume" and key in _LLM_TRAFFIC_ROW_KEYS:
            return True
    return False


def _meter_key_for_ref(ref: str, resolved_meters: List[dict]) -> str:
    ref_token = re.sub(r"[^a-z0-9]+", "", str(ref or "").strip().lower())
    for m in resolved_meters:
        row_key = _meter_row_key(m)
        if ref_token == re.sub(r"[^a-z0-9]+", "", row_key.lower()):
            return row_key
        desc = (m.get("description") or "").strip().lower()
        name = (m.get("name") or "").strip().lower()
        if ref_token in (
            re.sub(r"[^a-z0-9]+", "", desc),
            re.sub(r"[^a-z0-9]+", "", name),
        ):
            return row_key
    return str(ref or "").strip()


_ROW_FIELD_ALIASES = {
    "readable": ("readable", "key"),
    "key": ("key", "readable"),
    "label": ("label", "name"),
    "name": ("name", "label"),
    "router_ip": ("router_ip", "readable", "key"),
    "router_name": ("router_name", "name", "label"),
    "interface": ("interface", "label", "name"),
    "interface_description": ("interface_description", "description"),
    "description": ("description", "interface_description"),
}


def _row_field_value(row: dict, key: str) -> Any:
    """Read a row field, following common aliases used by LLM column specs."""
    if not isinstance(row, dict):
        return None
    if key in row and row[key] not in (None, ""):
        return row[key]
    for alias in _ROW_FIELD_ALIASES.get(key, ()):
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return row.get(key)


_HEADER_FIELD_HINTS = {
    "router ip": "router_ip",
    "router name": "router_name",
    "interface": "interface",
    "interface description": "interface_description",
    "in utilization": "recv_util",
    "out utilization": "xmit_util",
    "total utilization": "total_utilization",
}


def _resolve_column_key(key: str, resolved_meters: List[dict], row_keys: set, header: str = "") -> str:
    """Map a user/LLM column key to an actual row field key when possible."""
    key = str(key or "").strip()
    header_l = str(header or "").strip().lower()
    if header_l in _HEADER_FIELD_HINTS:
        hinted = _HEADER_FIELD_HINTS[header_l]
        if hinted in row_keys:
            return hinted

    if key and key in row_keys:
        return key
    for alias in _ROW_FIELD_ALIASES.get(key, ()):
        if alias in row_keys:
            return alias
    meter_key = _meter_key_for_ref(key, resolved_meters)
    if meter_key in row_keys:
        return meter_key
    return key


def _normalize_custom_columns(
    columns: List[dict],
    resolved_meters: List[dict],
    rows: List[dict],
    merge_columns: Optional[List[str]] = None,
) -> Tuple[List[dict], Optional[List[str]]]:
    """Rewrite column/merge keys to match assembled row fields."""
    if not columns or not rows:
        return columns, merge_columns
    row_keys = set()
    for row in rows:
        if isinstance(row, dict):
            row_keys.update(row.keys())

    normalized = []
    for col in columns:
        col = dict(col)
        col["key"] = _resolve_column_key(
            col.get("key") or col.get("header") or "",
            resolved_meters,
            row_keys,
            header=col.get("header") or "",
        )
        normalized.append(col)

    normalized_merge = None
    if merge_columns:
        normalized_merge = [
            _resolve_column_key(mk, resolved_meters, row_keys) for mk in merge_columns
        ]
    return normalized, normalized_merge


def _flowintf_fields_from_keyt(keyt, attrs: dict, router_names: dict) -> dict:
    """Derive router/interface columns for FlowIntfs topper rows."""
    router_key = str(keyt.key).split("_")[0]
    readable = keyt.readable or keyt.key
    label = _clean_key_label(keyt.label) if keyt.label else ""
    router_ip = readable.split("_")[0] if "_" in str(readable) else router_key
    router_name = router_names.get(router_key, router_ip)
    interface = label or (str(readable).split("_")[-1] if "_" in str(readable) else "")
    interface_description = (
        attrs.get("snmp.ifalias")
        or (keyt.description.strip() if keyt.description else "")
        or "-"
    )
    return {
        "router_key": router_key,
        "router_ip": router_ip,
        "router_name": router_name,
        "interface": interface,
        "interface_description": interface_description,
    }


def _apply_column_computations(
    rows: List[dict],
    columns: List[dict],
    resolved_meters: List[dict],
) -> List[dict]:
    """Populate sum_of / avg_of / sum_of_meters columns from existing row fields."""
    for col in columns:
        sum_refs = col.get("sum_of") or col.get("sum_keys") or col.get("sum_of_meters")
        avg_refs = col.get("avg_of") or col.get("avg_of_meters")
        if not sum_refs and not avg_refs:
            continue
        target = col.get("key")
        if not target:
            header = (col.get("header") or "computed").strip()
            target = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_") or "computed"
            col["key"] = target

        refs = avg_refs or sum_refs
        src_keys = [_meter_key_for_ref(r, resolved_meters) for r in refs]
        use_average = bool(avg_refs) or (
            col.get("format") in ("percent", "util_pct")
            and sum_refs
            and len(src_keys) > 1
        )
        for row in rows:
            values = [float(row.get(k) or 0) for k in src_keys]
            if not values:
                row[target] = None
            elif use_average:
                row[target] = sum(values) / len(values)
            else:
                row[target] = sum(values)
    return rows


def _finalize_report_columns(
    auto_columns: List[dict],
    columns: Optional[List[dict]],
    exclude_columns: Optional[List[str]],
    computed_columns: Optional[List[dict]],
) -> List[dict]:
    base = list(columns) if columns else list(auto_columns)
    if exclude_columns:
        excluded = {x.strip().lower() for x in exclude_columns}
        base = [
            c for c in base
            if c.get("key", "").lower() not in excluded
            and (c.get("header") or "").strip().lower() not in excluded
        ]
    if computed_columns:
        for comp in computed_columns:
            comp = dict(comp)
            comp_key = (comp.get("key") or "").strip().lower()
            comp_header = (comp.get("header") or "").strip().lower()
            merged = False
            for existing in base:
                ex_key = (existing.get("key") or "").strip().lower()
                ex_header = (existing.get("header") or "").strip().lower()
                if (comp_key and comp_key == ex_key) or (comp_header and comp_header == ex_header):
                    for field in (
                        "sum_of", "sum_keys", "sum_of_meters",
                        "avg_of", "avg_of_meters", "format", "format_args",
                    ):
                        if comp.get(field) is not None and existing.get(field) is None:
                            existing[field] = comp[field]
                    merged = True
                    break
            if not merged:
                base.append(comp)

    seen_pairs: set = set()
    seen_headers: set = set()
    deduped: List[dict] = []
    for col in base:
        key = (col.get("key") or "").strip().lower()
        header = (col.get("header") or "").strip().lower()
        pair = (key, header)
        if pair in seen_pairs:
            continue
        if header and header in seen_headers:
            continue
        seen_pairs.add(pair)
        if header:
            seen_headers.add(header)
        deduped.append(col)
    return deduped


def detect_assembled_traffic_hallucination(
    rows: List[dict],
    columns: Optional[List[dict]] = None,
) -> Optional[str]:
    if not rows:
        return None
    all_keys: set = set()
    for row in rows:
        if isinstance(row, dict):
            all_keys.update(row.keys())

    llm_traffic_keys = _LLM_TRAFFIC_ROW_KEYS.intersection({k.lower() for k in all_keys})
    string_times = sum(1 for row in rows if isinstance(row, dict) and _row_has_string_datetime(row))
    bucket_scaled = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("upload", "download", "upload_bytes", "download_bytes"):
            val = row.get(key)
            if _looks_like_bucket_scaled_volume(val):
                bucket_scaled += 1
                break

    if llm_traffic_keys and (
        string_times >= 1
        or bucket_scaled >= 2
        or _columns_use_volume_for_direction(columns)
    ):
        return (
            "Rejected LLM-assembled Trisul traffic rows (wrong scale and/or pre-formatted timestamps). "
            "Re-call generate_dynamic_report with the same counter_group_guid, keys, and time window. "
            "Use exclude_columns to drop columns and computed_columns (sum_of_meters) to add totals — "
            "never call get_key_traffic_data then generate_excel_report."
        )

    for total_key, recv_key, xmit_key in (
        ("total", "received", "transmitted"),
        ("total_bytes", "received_bytes", "transmitted_bytes"),
    ):
        if total_key not in all_keys:
            continue
        suspicious = 0
        for row in rows:
            try:
                total = float(row.get(total_key) or 0)
            except (TypeError, ValueError):
                continue
            if total <= 0:
                continue
            if recv_key in all_keys and float(row.get(recv_key) or 0) == 0:
                suspicious += 1
            if xmit_key in all_keys and float(row.get(xmit_key) or 0) == 0:
                suspicious += 1
        if suspicious >= 2:
            return (
                f"Detected {suspicious} zero directional values with non-zero totals. "
                "Use generate_dynamic_report with intent=key_traffic or source=topper — "
                "never merge topper lists manually."
            )
    return None


def _normalize_visualization(value: Optional[str]) -> str:
    """Return the canonical PDF visualization name."""
    normalized = str(value or "table").strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "table": "table",
        "tabular": "table",
        "line": "line",
        "line chart": "line",
        "area": "area",
        "area chart": "area",
        "pie": "pie",
        "pie chart": "pie",
    }
    if normalized not in aliases:
        raise ValueError(
            "visualization must be one of: table, line, area, pie"
        )
    return aliases[normalized]


def _chart_column(
    norm_cols: List[dict],
    rows: List[dict],
    requested: Optional[str] = None,
) -> dict:
    """Resolve one numeric chart column by key/header, or choose the first."""
    candidates = []
    for col in norm_cols:
        key = col.get("key")
        if not key or key in {"timestamp", "key", "name", "description"}:
            continue
        values = [row.get(key) for row in rows if row.get(key) is not None]
        if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            candidates.append(col)

    if requested:
        target = str(requested).strip().lower()
        for col in candidates:
            if target in {
                str(col.get("key") or "").strip().lower(),
                str(col.get("header") or "").strip().lower(),
            }:
                return col
        raise ValueError(f"chart series '{requested}' is not a numeric report column")

    if not candidates:
        raise ValueError("no numeric report column is available for the requested chart")
    return candidates[0]


def _chart_columns(
    norm_cols: List[dict],
    rows: List[dict],
    requested: Optional[List[str]] = None,
) -> List[dict]:
    """Resolve numeric series for line/area charts."""
    if requested:
        return [_chart_column(norm_cols, rows, item) for item in requested]

    result = []
    for col in norm_cols:
        try:
            resolved = _chart_column(norm_cols, rows, col.get("key"))
        except ValueError:
            continue
        if resolved not in result:
            result.append(resolved)
    if not result:
        raise ValueError("no numeric report columns are available for the requested chart")
    return result


def _chart_category_key(
    norm_cols: List[dict],
    rows: List[dict],
    requested: Optional[str] = None,
) -> str:
    """Resolve a pie-chart category column."""
    if requested:
        target = str(requested).strip().lower()
        for col in norm_cols:
            if target in {
                str(col.get("key") or "").strip().lower(),
                str(col.get("header") or "").strip().lower(),
            }:
                return col["key"]
        raise ValueError(f"chart category '{requested}' is not a report column")

    for key in ("name", "key", "label", "timestamp"):
        if any(row.get(key) not in (None, "") for row in rows):
            return key
    raise ValueError("no category column is available for the pie chart")


def _build_chart_image(
    visualization: str,
    title: str,
    columns: List[dict],
    rows: List[dict],
    chart_series: Optional[List[str]] = None,
    chart_category: Optional[str] = None,
):
    """Render a chart into an in-memory PNG from verified report rows."""
    from io import BytesIO
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates

    s = _lazy_server()
    norm_cols = s._normalize_excel_columns(columns)
    figure = Figure(figsize=(10, 5.8), dpi=160, constrained_layout=True)
    FigureCanvasAgg(figure)
    axis = figure.add_subplot(111)

    if visualization in {"line", "area"}:
        if not any(row.get("timestamp") is not None for row in rows):
            raise ValueError(
                f"{visualization} charts require time-series rows with timestamps; "
                "use intent='key_traffic'"
            )
        series_columns = _chart_columns(norm_cols, rows, chart_series)
        entity_values = {
            str(row.get("key") or row.get("name") or "").strip()
            for row in rows
        }
        use_entity_labels = len(entity_values - {""}) > 1

        for entity in sorted(entity_values):
            entity_rows = [
                row for row in rows
                if str(row.get("key") or row.get("name") or "").strip() == entity
            ]
            entity_rows.sort(key=lambda row: int(row["timestamp"]))
            x_values = [datetime.fromtimestamp(int(row["timestamp"])) for row in entity_rows]
            for col in series_columns:
                y_values = [float(row.get(col["key"]) or 0) for row in entity_rows]
                label = col["header"]
                if use_entity_labels and entity:
                    label = f"{entity} — {label}"
                line, = axis.plot(x_values, y_values, marker="o", linewidth=1.8, label=label)
                if visualization == "area":
                    axis.fill_between(x_values, y_values, 0, color=line.get_color(), alpha=0.20)

        axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        axis.xaxis.set_major_locator(mdates.AutoDateLocator())
        figure.autofmt_xdate()
        axis.set_xlabel("Time")
        axis.set_ylabel("Traffic")
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best")

    elif visualization == "pie":
        series_name = chart_series[0] if chart_series else None
        value_col = _chart_column(norm_cols, rows, series_name)
        category_key = _chart_category_key(norm_cols, rows, chart_category)
        totals: Dict[str, float] = {}
        for row in rows:
            category = row.get(category_key)
            if category_key == "timestamp" and category is not None:
                category = datetime.fromtimestamp(int(category)).strftime("%H:%M:%S")
            label = str(category if category not in (None, "") else "Unknown")
            totals[label] = totals.get(label, 0.0) + max(float(row.get(value_col["key"]) or 0), 0.0)
        totals = {label: value for label, value in totals.items() if value > 0}
        if not totals:
            raise ValueError("pie chart values are all zero")
        wedges, _, _ = axis.pie(
            list(totals.values()),
            startangle=90,
            autopct=lambda pct: f"{pct:.1f}%" if pct >= 3 else "",
        )
        axis.legend(
            wedges,
            list(totals.keys()),
            title=value_col["header"],
            loc="center left",
            bbox_to_anchor=(1.0, 0.5),
        )
        axis.axis("equal")

    axis.set_title(title)
    image = BytesIO()
    figure.savefig(image, format="png", bbox_inches="tight")
    image.seek(0)
    return image


def _section_flowables(payload: dict, styles) -> list:
    """Build the reportlab flowables for one report section (table or chart)."""
    s = _lazy_server()
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, Paragraph, Spacer, Image

    title = payload.get("title") or ""
    subtitle = payload.get("subtitle") or ""
    visualization = payload.get("visualization") or "table"
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []

    story = [Spacer(1, 5)]
    if title:
        story.append(Paragraph(title, styles["Heading2"]))
    if subtitle:
        story.append(Paragraph(f"<font color='#800080'>{subtitle}</font>", styles["Heading5"]))
    story.append(Spacer(1, 12))

    if visualization == "table":
        norm_cols = s._normalize_excel_columns(columns)
        data = [[c["header"] for c in norm_cols]]
        for row in rows:
            data.append(s._excel_row_values(row, norm_cols))
        table = Table(data, repeatRows=1)
        table.setStyle(s.trisul_table_style())
        story.append(table)
    else:
        image_data = _build_chart_image(
            visualization, title, columns, rows,
            chart_series=payload.get("chart_series"),
            chart_category=payload.get("chart_category"),
        )
        story.append(Image(image_data, width=7.2 * inch, height=4.2 * inch, kind="proportional"))
    return story


def _build_pdf_document(payloads, filename, report_title, from_ts, to_ts) -> str:
    """Write one PDF where each payload becomes its own page."""
    s = _lazy_server()
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet

    filepath = filename if filename.startswith("/tmp/") else f"/tmp/{filename}"
    pdf = SimpleDocTemplate(
        filepath, pagesize=A4, leftMargin=15, rightMargin=15, topMargin=75, bottomMargin=70,
    )
    styles = getSampleStyleSheet()

    story = []
    for index, payload in enumerate(payloads):
        story.extend(_section_flowables(payload, styles))
        if index < len(payloads) - 1:
            story.append(PageBreak())

    decorator = s.trisul_page_decorator(report_title, from_ts, to_ts)
    pdf.build(story, onFirstPage=decorator, onLaterPages=decorator)
    return filepath


class ReportDataError(Exception):
    """Raised when a report section cannot be assembled from Trisul data."""


def _collect_section_payload(
    counter_group_guid: str,
    intent: str = "auto",
    source: str = "auto",
    keys: Optional[List[str]] = None,
    meters: Optional[List[str]] = None,
    duration_secs: int = 3600,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    max_count: Optional[int] = None,
    sort_meter: int = 0,
    row_layout: str = "auto",
    visualization: str = "table",
    chart_series: Optional[List[str]] = None,
    chart_category: Optional[str] = None,
    columns: Optional[List[dict]] = None,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    context: str = "context0",
    zmq_endpoint: Optional[str] = None,
    merge_columns: Optional[List[str]] = None,
    exclude_columns: Optional[List[str]] = None,
    computed_columns: Optional[List[dict]] = None,
) -> dict:
    """Fetch and verify the rows for a single report section (no file written)."""
    s = _lazy_server()
    from trisul_ai_cli import trp_pb2

    counter_group_guid = str(counter_group_guid).strip()
    duration_secs = int(duration_secs)
    sort_meter = int(sort_meter)
    visualization = _normalize_visualization(visualization)

    if not zmq_endpoint:
        ctx = s.normalize_context(context)
        zmq_endpoint = f"ipc:///usr/local/var/lib/trisul-hub/domain0/hub0/{ctx}/run/trp_0"

    data_type, resolved_source, resolved_layout = _resolve_intent_and_source(
        intent, source, row_layout, keys, max_count
    )

    if data_type == DATA_TYPE_TOPPER and not max_count:
        max_count = 10

    from_ts_val, to_ts_val = s._get_time_window(
        zmq_endpoint, duration_secs, start_ts, end_ts, start_time, end_time
    )

    is_flowintfs = counter_group_guid.upper() == s.FLOWINTFS_GUID.upper()

    if meters is None:
        if is_flowintfs and data_type == DATA_TYPE_TOPPER:
            inferred_util = _infer_util_meter_refs(columns, computed_columns)
            if inferred_util:
                meters = inferred_util
                logging.info(f"[report_engine] inferred FlowIntfs util meters: {meters}")
        if meters is None:
            if resolved_source == "key_timeseries":
                meters = ["Total", "Into Homenet", "Outof Homenet"]
                try:
                    s._resolve_meter_ids(counter_group_guid, meters, zmq_endpoint)
                except Exception:
                    meters = ["0", "1", "2"]
            else:
                meters = ["0", "1", "2"]

    resolved_meters, cg_group = s._resolve_meter_ids(counter_group_guid, meters, zmq_endpoint)
    meter_ids = [m["id"] for m in resolved_meters]
    meters_info = {m["id"]: m for m in resolved_meters}

    if is_flowintfs and meter_ids and all(
        _is_pct_meter(meters_info[mid]) for mid in meter_ids
    ):
        if sort_meter not in meter_ids:
            sort_meter = 4 if 4 in meter_ids else meter_ids[0]
    bucket_secs = _bucket_size_secs(cg_group)
    cg_name = cg_group.get("name", "Counter Group")

    logging.info(
        f"[report_engine] data_type={data_type} source={resolved_source} "
        f"layout={resolved_layout} keys={keys} max_count={max_count}"
    )

    rows: List[dict] = []
    report_merge = merge_columns
    key_entries: List[dict] = []
    stat_bucket_secs = bucket_secs
    bucket_warning: Optional[str] = None

    if keys:
        for k in keys:
            key_entries.append(_search_resolve_key(
                s.get_response, trp_pb2, zmq_endpoint, counter_group_guid, k
            ))

    if resolved_source == "key_timeseries":
        if not key_entries:
            raise ReportDataError("keys required for key traffic reports.")
        for i, entry in enumerate(key_entries):
            user_key = keys[i] if keys and i < len(keys) else entry.get("lookup", "")
            resp = _fetch_key_timeseries(
                s.get_response, trp_pb2, zmq_endpoint,
                counter_group_guid, entry, from_ts_val, to_ts_val, user_key,
            )
            if not resp.stats:
                logging.warning(f"[report_engine] no stats for key {entry['lookup']}")
                continue
            stat_bucket_secs = _infer_bucket_secs_from_stats(list(resp.stats), bucket_secs)
            if stat_bucket_secs != bucket_secs:
                logging.info(
                    f"[report_engine] key={entry['lookup']} using stat bucket "
                    f"{stat_bucket_secs}s (cg bucket {bucket_secs}s)"
                )
            rows.extend(
                _build_timeseries_rows(resp, entry, meter_ids, meters_info, stat_bucket_secs)
            )
        bucket_warning = _key_traffic_bucket_warning(
            cg_group, stat_bucket_secs, duration_secs, len(rows)
        )
        if bucket_warning:
            logging.warning(f"[report_engine] {bucket_warning}")

    elif resolved_source == "topper":
        mc = int(max_count or 10)
        if sort_meter not in meters_info:
            sr, _ = s._resolve_meter_ids(counter_group_guid, [str(sort_meter)], zmq_endpoint)
            sort_meter = sr[0]["id"] if sr else sort_meter

        topper_keys = _fetch_cg_topper(
            s.get_response, trp_pb2, zmq_endpoint, counter_group_guid,
            sort_meter, mc, from_ts_val, to_ts_val,
            s._is_system_key, s._is_interface_key, s.FLOWINTFS_GUID,
        )
        if not topper_keys:
            raise ReportDataError("No topper keys found.")

        router_names = {}
        if is_flowintfs:
            router_keys = {
                keyt.key.split("_")[0]
                for keyt in topper_keys
                if s._is_interface_key(keyt.key)
            }
            router_names = s._fetch_router_names(router_keys, zmq_endpoint)

        key_entries = []
        for keyt in topper_keys:
            lookup = keyt.readable or keyt.label or keyt.key
            attrs = s._key_attrs_to_dict(keyt)
            entry = {
                "lookup": lookup,
                "internal_key": keyt.key,
                "readable": keyt.readable or keyt.key,
                "label": _clean_key_label(keyt.label) if keyt.label else lookup,
                "description": attrs.get("snmp.ifalias") or (keyt.description or ""),
                "attrs": attrs,
            }
            if is_flowintfs:
                entry.update(_flowintf_fields_from_keyt(keyt, attrs, router_names))
            key_entries.append(entry)

        use_webtrisul_util = is_flowintfs and meter_ids and all(
            _is_pct_meter(meters_info[mid]) for mid in meter_ids
        )

        if resolved_layout == "per_key_meter":
            rows = _build_per_key_meter_rows(
                s._get_key_meter_stats, s.fmt_volume, s.fmt_bw,
                counter_group_guid, key_entries, meter_ids, meters_info,
                from_ts_val, to_ts_val, zmq_endpoint,
            )
            report_merge = report_merge or ["name"]
        elif use_webtrisul_util:
            bw_meters, _ = s._resolve_meter_ids(
                counter_group_guid, ["Recv", "Xmit"], zmq_endpoint
            )
            bw_meter_ids = [m["id"] for m in bw_meters]
            bw_meters_info = {m["id"]: m for m in bw_meters}
            topper_bucket_secs = _topper_bucket_secs(cg_group)
            logging.info(
                "[report_engine] FlowIntfs util via webtrisul formula "
                f"(retro latest Recv/Xmit + ifspeed, crop={topper_bucket_secs}s), "
                f"bw_meters={bw_meter_ids}"
            )
            rows = _build_flowintf_util_rows(
                s.get_response,
                trp_pb2,
                s._get_key_meter_stats,
                counter_group_guid,
                key_entries,
                meters_info,
                from_ts_val,
                to_ts_val,
                zmq_endpoint,
                bw_meter_ids,
                bw_meters_info,
                topper_bucket_secs,
            )
            rows.sort(key=lambda r: r.get("_sort_total", 0), reverse=True)
            for row in rows:
                row.pop("_sort_total", None)
        else:
            rows = _build_aggregate_rows(
                s._get_key_meter_stats,
                counter_group_guid,
                key_entries,
                meter_ids,
                meters_info,
                from_ts_val,
                to_ts_val,
                zmq_endpoint,
            )
            rows.sort(key=lambda r: r.get("_sort_total", 0), reverse=True)
            for row in rows:
                row.pop("_sort_total", None)

    elif resolved_source == "key_stats":
        if not key_entries:
            raise ReportDataError("keys required.")
        if resolved_layout == "per_key_meter":
            rows = _build_per_key_meter_rows(
                s._get_key_meter_stats, s.fmt_volume, s.fmt_bw,
                counter_group_guid, key_entries, meter_ids, meters_info,
                from_ts_val, to_ts_val, zmq_endpoint,
            )
            report_merge = report_merge or ["name"]
        else:
            rows = _build_aggregate_rows(
                s._get_key_meter_stats, counter_group_guid, key_entries,
                meter_ids, meters_info, from_ts_val, to_ts_val, zmq_endpoint,
            )

    if not rows:
        raise ReportDataError("No data rows collected from Trisul.")

    auto_columns = (
        _flowintf_auto_columns(resolved_meters)
        if (
            not columns
            and data_type == DATA_TYPE_TOPPER
            and counter_group_guid.upper() == s.FLOWINTFS_GUID.upper()
        )
        else _auto_columns(resolved_layout, resolved_meters, data_type)
    )
    columns = _finalize_report_columns(
        auto_columns, columns, exclude_columns, computed_columns,
    )
    rows = _apply_column_computations(rows, columns, resolved_meters)
    columns, report_merge = _normalize_custom_columns(
        columns, resolved_meters, rows, report_merge,
    )

    verification = _verify_report(
        data_type, resolved_source, rows, keys, max_count, from_ts_val, to_ts_val,
        stat_bucket_secs=stat_bucket_secs if data_type == DATA_TYPE_KEY_TRAFFIC else None,
        duration_secs=duration_secs if data_type == DATA_TYPE_KEY_TRAFFIC else None,
        cg_group=cg_group if data_type == DATA_TYPE_KEY_TRAFFIC else None,
        columns=columns,
        resolved_meters=resolved_meters,
    )
    if not verification["verified"]:
        logging.warning(f"[report_engine] verification issues: {verification['issues']}")

    if not title:
        if data_type == DATA_TYPE_KEY_TRAFFIC:
            key_names = ", ".join(keys or [])
            title = f"{key_names} Key Traffic — {cg_name}"
        elif data_type == DATA_TYPE_TOPPER:
            title = f"Top {len(rows)} {cg_name}"
        else:
            title = f"{cg_name} Report"

    column_headers = [c.get("header") or c.get("key") for c in s._normalize_excel_columns(columns)]
    payload = {
        "title": title,
        "subtitle": subtitle,
        "columns": columns,
        "rows": rows,
        "column_headers": column_headers,
        "visualization": visualization,
        "chart_series": chart_series,
        "chart_category": chart_category,
        "report_merge": report_merge,
        "data_type": data_type,
        "source": resolved_source,
        "row_layout": resolved_layout,
        "verification": verification,
        "from_ts": from_ts_val,
        "to_ts": to_ts_val,
        "duration": s.epoch_to_duration(from_ts_val, to_ts_val),
    }
    if data_type == DATA_TYPE_KEY_TRAFFIC:
        payload["bucket_interval_secs"] = stat_bucket_secs
        if bucket_warning:
            payload["warning"] = bucket_warning
    return payload


def _report_error(exc: Exception) -> dict:
    """Normalize an exception into the tool's error contract."""
    logging.error(f"[report_engine] Error: {exc}", exc_info=True)
    msg = str(exc)
    if "ZMQ timeout" in msg and "ipc://" in msg:
        msg = (
            f"{msg} — no local Trisul TRP on IPC. "
            "Pass zmq_endpoint (e.g. tcp://host:port) or connect in the CLI first."
        )
    return {"status": "error", "message": msg, "file_path": None}


def _auto_filename(title: str, ext: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title or "report")[:40]
    return f"report_{safe}_{int(datetime.now().timestamp())}.{ext}"


def run_dynamic_report(
    counter_group_guid: Optional[str] = None,
    intent: str = "auto",
    source: str = "auto",
    keys: Optional[List[str]] = None,
    meters: Optional[List[str]] = None,
    duration_secs: int = 3600,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    max_count: Optional[int] = None,
    sort_meter: int = 0,
    row_layout: str = "auto",
    output_format: str = "xlsx",
    visualization: str = "table",
    chart_series: Optional[List[str]] = None,
    chart_category: Optional[str] = None,
    columns: Optional[List[dict]] = None,
    title: Optional[str] = None,
    filename: Optional[str] = None,
    sheet_name: str = "Report",
    context: str = "context0",
    zmq_endpoint: Optional[str] = None,
    merge_columns: Optional[List[str]] = None,
    exclude_columns: Optional[List[str]] = None,
    computed_columns: Optional[List[dict]] = None,
    sections: Optional[List[dict]] = None,
    report_title: Optional[str] = None,
) -> dict:
    s = _lazy_server()
    try:
        output_format = (output_format or "xlsx").lower()

        if sections:
            return _run_multi_section_report(
                sections=sections,
                report_title=report_title or title,
                filename=filename,
                output_format=output_format,
                counter_group_guid=counter_group_guid,
                duration_secs=duration_secs,
                start_ts=start_ts,
                end_ts=end_ts,
                start_time=start_time,
                end_time=end_time,
                context=context,
                zmq_endpoint=zmq_endpoint,
            )

        visualization = _normalize_visualization(visualization)
        if visualization != "table" and output_format != "pdf":
            raise ValueError("line, area, and pie visualizations require output_format='pdf'")
        if not counter_group_guid:
            raise ValueError("counter_group_guid is required (or pass sections=[...])")

        payload = _collect_section_payload(
            counter_group_guid=counter_group_guid,
            intent=intent,
            source=source,
            keys=keys,
            meters=meters,
            duration_secs=duration_secs,
            start_ts=start_ts,
            end_ts=end_ts,
            start_time=start_time,
            end_time=end_time,
            max_count=max_count,
            sort_meter=sort_meter,
            row_layout=row_layout,
            visualization=visualization,
            chart_series=chart_series,
            chart_category=chart_category,
            columns=columns,
            title=title,
            context=context,
            zmq_endpoint=zmq_endpoint,
            merge_columns=merge_columns,
            exclude_columns=exclude_columns,
            computed_columns=computed_columns,
        )

        ext = "pdf" if output_format == "pdf" else "xlsx"
        filename = filename or _auto_filename(payload["title"], ext)

        if output_format == "pdf":
            filepath = _build_pdf_document(
                [payload], filename, payload["title"],
                payload["from_ts"], payload["to_ts"],
            )
        else:
            filepath = s._build_excel_report(
                columns=payload["columns"], rows=payload["rows"], title=payload["title"],
                from_ts=payload["from_ts"], to_ts=payload["to_ts"], filename=filename,
                sheet_name=sheet_name, merge_columns=payload["report_merge"],
            )

        if not os.path.isfile(filepath):
            return {"status": "error", "message": f"File not written: {filepath}", "file_path": None}

        column_headers = payload["column_headers"]
        result = {
            "status": "success",
            "message": f"Report generated at {filepath}",
            "file_path": filepath,
            "row_count": len(payload["rows"]),
            "columns": column_headers,
            "data_type": payload["data_type"],
            "source": payload["source"],
            "row_layout": payload["row_layout"],
            "output_format": output_format,
            "visualization": payload["visualization"],
            "verification": payload["verification"],
            "duration": payload["duration"],
            "reply_guidance": (
                f"Confirm that the PDF uses the '{payload['visualization']}' visualization. "
                "The source columns are, in this exact order: "
                f"{', '.join(column_headers)}. "
                "Do not describe a chart as a table or claim a different visualization."
            ),
        }
        if payload["data_type"] == DATA_TYPE_KEY_TRAFFIC:
            result["bucket_interval_secs"] = payload.get("bucket_interval_secs")
            if payload.get("warning"):
                result["warning"] = payload["warning"]
        return result

    except Exception as e:
        return _report_error(e)


def _run_multi_section_report(
    sections: List[dict],
    report_title: Optional[str] = None,
    filename: Optional[str] = None,
    output_format: str = "pdf",
    counter_group_guid: Optional[str] = None,
    duration_secs: int = 3600,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    context: str = "context0",
    zmq_endpoint: Optional[str] = None,
) -> dict:
    """Build one multi-page PDF where each section is fetched and verified server-side."""
    if output_format != "pdf":
        raise ValueError("sections require output_format='pdf'")
    if not isinstance(sections, (list, tuple)) or not sections:
        raise ValueError("sections must be a non-empty list of section specs")

    shared = {
        "counter_group_guid": counter_group_guid,
        "duration_secs": duration_secs,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_time": start_time,
        "end_time": end_time,
        "context": context,
        "zmq_endpoint": zmq_endpoint,
    }
    allowed = {
        "counter_group_guid", "intent", "source", "keys", "meters", "duration_secs",
        "start_ts", "end_ts", "start_time", "end_time", "max_count", "sort_meter",
        "row_layout", "visualization", "chart_series", "chart_category", "columns",
        "title", "subtitle", "context", "zmq_endpoint", "merge_columns",
        "exclude_columns", "computed_columns",
    }

    payloads: List[dict] = []
    for index, raw_section in enumerate(sections, start=1):
        if not isinstance(raw_section, dict):
            raise ValueError(f"section {index} must be an object")
        unknown = set(raw_section) - allowed
        if unknown:
            raise ValueError(
                f"section {index} has unsupported fields: {', '.join(sorted(unknown))}"
            )
        spec = {k: v for k, v in shared.items() if v is not None}
        spec.update({k: v for k, v in raw_section.items() if v is not None})
        if not spec.get("counter_group_guid"):
            raise ValueError(f"section {index} is missing counter_group_guid")

        logging.info(
            f"[report_engine] section {index}/{len(sections)} "
            f"guid={spec['counter_group_guid']} intent={spec.get('intent', 'auto')} "
            f"visualization={spec.get('visualization', 'table')}"
        )
        try:
            payloads.append(_collect_section_payload(**spec))
        except ReportDataError as exc:
            raise ReportDataError(f"section {index} ({spec.get('title') or 'untitled'}): {exc}")

    from_ts = min(p["from_ts"] for p in payloads)
    to_ts = max(p["to_ts"] for p in payloads)
    report_title = report_title or payloads[0]["title"]
    filename = filename or _auto_filename(report_title, "pdf")

    filepath = _build_pdf_document(payloads, filename, report_title, from_ts, to_ts)
    if not os.path.isfile(filepath):
        return {"status": "error", "message": f"File not written: {filepath}", "file_path": None}

    s = _lazy_server()
    section_summaries = [
        {
            "page": index,
            "title": p["title"],
            "visualization": p["visualization"],
            "data_type": p["data_type"],
            "row_count": len(p["rows"]),
            "columns": p["column_headers"],
            "verification": p["verification"],
        }
        for index, p in enumerate(payloads, start=1)
    ]
    guidance = "; ".join(
        f"page {sec['page']} is a {sec['visualization']} of {sec['title']}"
        for sec in section_summaries
    )
    return {
        "status": "success",
        "message": f"Report generated at {filepath}",
        "file_path": filepath,
        "output_format": "pdf",
        "page_count": len(payloads),
        "sections": section_summaries,
        "row_count": sum(len(p["rows"]) for p in payloads),
        "duration": s.epoch_to_duration(from_ts, to_ts),
        "reply_guidance": (
            f"This is ONE PDF with {len(payloads)} pages: {guidance}. "
            "Report a single file path and describe each page's visualization exactly. "
            "Do not claim separate files were created."
        ),
    }


def run_dynamic_excel_report(**kwargs) -> dict:
    """Backward-compatible wrapper."""
    kwargs.setdefault("output_format", "xlsx")
    return run_dynamic_report(**kwargs)
