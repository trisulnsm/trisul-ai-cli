"""Build importable Webtrisul dashboard packages.

The output of :func:`build_package` is the exact 3-element array that
``PackageController#insert_dashboard_and_modules`` reads:
``[[package], [dashboard], [module, ...]]``.

Validation is deliberately kept out of the MCP layer so ``server.py`` only
carries thin tool wrappers. Live Trisul lookups are injected as callables
(see :class:`Resolvers`) so this module never imports ``server``.
"""

import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
CATALOG_PATH = RESOURCES_DIR / "dashboard_module_catalog.json"
COUNTERGROUPS_CATALOG_PATH = RESOURCES_DIR / "countergroups_catalog.json"
GUID_MAP_PATH = RESOURCES_DIR / "trisul_guid_map.json"

MODURI = "/webtrisul_module/getmod"
AXUPDATEURI = "/newdash/traffic_axsavepos"

# WebtrisulDashboard validates key against this regex.
DASHBOARD_KEY_RE = re.compile(r"^[A-Za-z0-9]+$")
GUID_RE = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$")

# Option names that hold a GUID. Counter groups are resolved live; only alert
# and resource groups use the non-counter-group reference map.
GUID_OPTIONS = {
    "cgguid": "counter_groups",
    "counter_group": "counter_groups",
    "alert_group": "alert_groups",
    "rguid": "resource_groups",
}

# Options that name a key inside the module's own counter group.
KEY_OPTIONS = ("key",)

# ManageKeysController#key_matches synthesizes these keys only when the counter
# group is GuidMap::GUID_CG_AGGREGATE. They do not exist as items in the host,
# application or any other counter group, so a module using them elsewhere
# renders empty.
GUID_CG_AGGREGATE = "{393B5EBC-AB41-4387-8F31-8077DB917336}"
AGGREGATE_ONLY_KEYS = {
    "TOTALBW",
    "DIR_INTOHOME",
    "DIR_OUTOFHOME",
    "DIR_WITHINHOME",
    "DIR_TRANSIT",
}

# Webtrisul reads these with JSON.parse on a string (TrpjsController#apex_chart),
# so they must be stored as JSON text, never as a nested array.
JSON_STRING_OPTIONS = ("models", "refmodel")

# Webtrisul passes these chart flags straight through as query parameters, where
# a null becomes an empty value the chart cannot read. An unset flag means "off",
# so it is written as 0.
ZERO_WHEN_UNSET_OPTIONS = ("pcap_range_marker",)

# The meter (statistic index) selects which metric of the counter group a module
# shows. Templates name it either statid or meter; models-driven templates carry
# it per series instead. Meter 0 is Total by convention.
METER_OPTION_NAMES = ("statid", "meter")

# Trisul host keyformat, e.g. C0.A8.0A.0D — modules must store the readable IP instead.
HOST_KEYFORMAT_RE = re.compile(r"^((([0-9A-Fa-f]){2}\.){3}([0-9A-Fa-f]){2})$")

# Module ids in the generated package are placeholders: the importer renumbers
# them from WebtrisulModule.last.id via WebtrisulDashboard#offset_modids, which
# rewrites pos_flexi with a word-boundary gsub per mapping pair. That rewrite
# corrupts the layout if a newly assigned id collides with a not-yet-rewritten
# placeholder, so placeholders start far above any realistic live module id.
DEFAULT_BASE_MODULE_ID = 900001
DEFAULT_DASHBOARD_ID = 900000
REALTIME_TEMPLATE_IDS = {54, 56, 105}
EXPLICIT_REALTIME_TERMS = {"live", "realtime"}
# Retro modules that have a live stabber counterpart. On a realtime/live
# dashboard these must be swapped for 54 / 56 / 105.
RETRO_WHEN_REALTIME = {1, 3, 61, 101, 102, 103}
REALTIME_REPLACEMENT_HINT = {
    1: "56 (Real time toppers list)",
    3: "56 (Real time toppers list)",
    61: "105 (Real time single value)",
    101: "54 (Real time key traffic chart)",
    102: "54 for a named-key live chart, or 56 for a live toppers list "
         "(there is no live pie/donut toppers chart)",
    103: "54 (Real time key traffic chart)",
}

# Single-value badges hide their panel frame by default. Other templates never
# appear in WebtrisulDashboard.undecoratedmods.
UNDECORATED_TEMPLATE_IDS = {61, 105}

# A module's catalog "presentation" is the shape the panel actually renders.
# Comparing it against the presentation the user asked for is what stops a
# request for an "https traffic chart" from being built as a toppers table.
PRESENTATION_LABELS = {
    "table": "table of top keys",
    "timeseries_chart": "time-series chart",
    "kpi": "single-value badge",
    "tree": "expandable crosskey tree",
    "sankey": "sankey diagram",
    "embed": "embedded Webtrisul page",
    "alert_feed": "alert feed",
    "resource_table": "resources table",
}

# Wording the user reaches for, mapped to every presentation that can satisfy
# it. Matched on word boundaries so "online" is not read as "line".
# "flow map" is handled separately as the session flowmap (embed), not a chart.
PRESENTATION_WORDS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("sankey",), ("sankey",)),
    (("tree", "drilldown", "drill down"), ("tree",)),
    (
        ("pie", "donut", "doughnut", "breakdown", "share", "proportion", "distribution"),
        ("timeseries_chart",),
    ),
    (
        ("chart", "charts", "graph", "graphs", "plot", "plotted", "trend", "trends",
         "trending", "timeseries", "time series", "over time", "timeline", "history",
         "historical", "line", "area", "mrtg", "histogram", "sparkline", "curve"),
        ("timeseries_chart",),
    ),
    (
        ("table", "tabular", "list", "listing", "grid", "rows", "top talkers", "leaderboard"),
        ("table", "resource_table", "alert_feed", "tree"),
    ),
    (
        ("badge", "kpi", "gauge", "tile", "card", "single value", "one number",
         "headline", "counter"),
        ("kpi",),
    ),
)

_FLOWMAP_RE = re.compile(r"\bflow ?maps?\b")
_TOPPER_INTENT_RE = re.compile(r"\btop(?:pers?)?\b|\btop\s*\d+\b")
_CROSSKEY_NAME_RE = re.compile(r"(_X_|_x_|_bx_|\sx\s)", re.I)
_REALTIME_TEXT_RE = re.compile(
    r"\b(?:real[\s-]?time|realtime|live(?:[\s-]?updating)?)\b",
    re.I,
)

_catalog_cache: Optional[dict] = None
_countergroups_catalog_cache: Optional[dict] = None
_guid_map_cache: Optional[dict] = None


def load_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is None:
        with open(CATALOG_PATH, "r", encoding="utf-8") as fh:
            _catalog_cache = json.load(fh)
    return _catalog_cache


def load_countergroups_catalog() -> dict:
    global _countergroups_catalog_cache
    if _countergroups_catalog_cache is None:
        with open(COUNTERGROUPS_CATALOG_PATH, "r", encoding="utf-8") as fh:
            _countergroups_catalog_cache = json.load(fh)
    return _countergroups_catalog_cache


def find_derived_counter_group_types(query: str = "") -> List[dict]:
    """Keyword match over derived counter-group type, scenarios, and module usage."""
    catalog = load_countergroups_catalog()
    types = catalog.get("types", [])
    terms = [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 2]
    if not terms:
        return types

    type_aliases = {
        "crosskey": ("crosskey", "cross", "sankey", "tree", "dimension", "drill"),
        "filter": ("filter", "filtered", "narrow", "restrict", "include", "exclude"),
        "keyset": ("keyset", "bucket", "buckets", "aggregate", "class", "farm"),
    }
    wanted_types = set()
    for type_name, aliases in type_aliases.items():
        if any(term in aliases or term == type_name for term in terms):
            wanted_types.add(type_name)
    if wanted_types:
        typed = [entry for entry in types if entry.get("type") in wanted_types]
        if typed:
            types = typed

    def _blob(entry: dict) -> str:
        parts = [
            str(entry.get("type") or ""),
            str(entry.get("name") or ""),
            str(entry.get("what_it_is") or ""),
            str(entry.get("use_when") or ""),
            str(entry.get("not_when") or ""),
            str(entry.get("create_tool") or ""),
        ]
        for scenario in entry.get("scenarios") or []:
            parts.extend(str(value) for value in scenario.values())
        return " ".join(parts).lower()

    scored: List[Tuple[int, dict]] = []
    for entry in types:
        blob = _blob(entry)
        score = sum(1 for term in terms if term in blob)
        if score:
            scored.append((score, entry))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored]
    return types


def derived_counter_group_types_for_llm(query: str = "") -> dict:
    """Catalog payload for the LLM, including choose-table and reuse rules."""
    catalog = load_countergroups_catalog()
    return {
        "reuse_first": catalog.get("reuse_first"),
        "how_to_choose": catalog.get("how_to_choose", []),
        "show_in_module_overview": catalog.get("show_in_module_overview", {}),
        "types": deepcopy(find_derived_counter_group_types(query)),
        "counter_group_guid_source": (
            "Resolve every counter-group GUID with a live COUNTER_GROUP_INFO request. "
            "Never copy a GUID from this catalog."
        ),
    }


def load_guid_map() -> dict:
    global _guid_map_cache
    if _guid_map_cache is None:
        with open(GUID_MAP_PATH, "r", encoding="utf-8") as fh:
            _guid_map_cache = json.load(fh)
    return _guid_map_cache


def is_likely_crosskey_name(name: str) -> bool:
    """True when a live counter-group name looks like a crosskey (A_X_B, A_bx_B)."""
    return bool(_CROSSKEY_NAME_RE.search(name or ""))


def has_explicit_realtime(text: str) -> bool:
    """True only for explicit real time / realtime / live wording.

    Words such as current, latest, last, now, or bandwidth do not count.
    """
    return bool(_REALTIME_TEXT_RE.search(text or ""))


def get_template(template_id: Any) -> Optional[dict]:
    try:
        wanted = int(template_id)
    except (TypeError, ValueError):
        return None
    for tpl in load_catalog()["templates"]:
        if tpl["template_id"] == wanted:
            return tpl
    return None


def requested_presentations(text: str) -> Tuple[set, List[str]]:
    """Read the presentation the user asked for out of their own wording.

    Returns the set of template presentations that can satisfy the wording and
    the words that were recognised. An empty set means the wording said nothing
    about presentation, so any module shape is acceptable.
    """
    lowered = f" {str(text or '').lower()} "
    allowed: set = set()
    matched: List[str] = []

    # "flow map" is the session flowmap module (template 107), not a chart.
    if _FLOWMAP_RE.search(lowered):
        lowered = _FLOWMAP_RE.sub(" flowmap_module ", lowered)
        allowed.add("embed")
        matched.append("flowmap")

    for words, presentations in PRESENTATION_WORDS:
        for word in words:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                allowed.update(presentations)
                matched.append(word)
                break
    return allowed, matched


def templates_with_presentation(presentations: set) -> List[dict]:
    return [
        tpl for tpl in load_catalog()["templates"]
        if tpl.get("presentation") in presentations
    ]


def find_templates(query: str = "") -> List[dict]:
    """Keyword match over template name, group, use_when and looks_like."""
    templates = load_catalog()["templates"]
    terms = [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 2]
    if not terms:
        return templates

    # A query that names a presentation ("... chart", "... list") must not offer
    # modules of another shape, otherwise a chart request gets built as a table.
    wanted_presentations, _ = requested_presentations(query)
    if wanted_presentations:
        by_presentation = [
            tpl for tpl in templates
            if tpl.get("presentation") in wanted_presentations
        ]
        templates = by_presentation or templates

    # Stabber-backed modules are opt-in. In particular, words such as "last",
    # "latest" and "current" must not make a request real-time.
    explicit_realtime = (
        any(term in EXPLICIT_REALTIME_TERMS for term in terms)
        or ("real" in terms and "time" in terms)
    )
    if not explicit_realtime:
        templates = [
            tpl for tpl in templates
            if tpl.get("template_id") not in REALTIME_TEMPLATE_IDS
        ]

    scored = []
    for tpl in templates:
        haystack = " ".join(
            str(tpl.get(field, ""))
            for field in ("name", "group", "use_when", "looks_like", "notes")
        ).lower()
        score = sum(1 for term in terms if term in haystack)
        if explicit_realtime and tpl.get("template_id") in REALTIME_TEMPLATE_IDS:
            score += 100
            if wanted_presentations and tpl.get("presentation") in wanted_presentations:
                score += 50
        if score:
            scored.append((score, tpl))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [tpl for _, tpl in scored] or templates


def templates_for_llm(query: str = "") -> List[dict]:
    """Return catalog entries without reusable static counter-group GUIDs.

    The source catalog mirrors Webtrisul defaults and examples, which contain
    installation-specific counter-group GUIDs. Replacing those values ensures
    the agent must issue a live counter-group-info request before generation.
    """
    templates = deepcopy(find_templates(query))
    for template in templates:
        for container_name in ("defaults", "example_options"):
            container = template.get(container_name, {})
            for option_name in ("cgguid", "counter_group"):
                if option_name in container:
                    container[option_name] = "<GUID_FROM_LIVE_COUNTER_GROUP_INFO>"
            raw_models = container.get("models")
            if isinstance(raw_models, str):
                try:
                    models = json.loads(raw_models)
                except json.JSONDecodeError:
                    continue
                for model in models if isinstance(models, list) else []:
                    if isinstance(model, dict) and "cgguid" in model:
                        model["cgguid"] = "<GUID_FROM_LIVE_COUNTER_GROUP_INFO>"
                container["models"] = json.dumps(models, separators=(",", ":"))
    return templates


def normalize_guid(value: Any) -> str:
    text = str(value or "").strip()
    if text and not text.startswith("{"):
        text = "{" + text
    if text and not text.endswith("}"):
        text = text + "}"
    return text.upper()


def slugify_key(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", str(name or ""))
    return slug or "dashboard"


class Resolvers:
    """Live-Trisul lookups injected by the MCP layer.

    Counter groups and keys must be verified against the connected Trisul.
    A failed live lookup blocks dashboard generation; static counter-group
    GUIDs are intentionally never used as a fallback.
    """

    def __init__(
        self,
        counter_groups: Optional[Callable[[], Dict[str, str]]] = None,
        key_lookup: Optional[Callable[[str, str], List[dict]]] = None,
    ):
        self._counter_groups = counter_groups
        self._key_lookup = key_lookup
        self._cg_cache: Optional[Dict[str, str]] = None
        # Each TRP call blocks for the full ZMQ timeout, so one failure marks the
        # instance unreachable and every later lookup short-circuits.
        self._unreachable = False

    def counter_groups(self) -> Optional[Dict[str, str]]:
        """Return {normalized_guid: name} or None when Trisul is unreachable."""
        if self._cg_cache is not None or self._unreachable:
            return self._cg_cache
        if self._counter_groups is None:
            self._unreachable = True
            return None
        try:
            groups = self._counter_groups() or {}
            self._cg_cache = {normalize_guid(g): n for g, n in groups.items()}
        except Exception as exc:
            logging.warning(f"[dashboard_builder] counter group lookup failed: {exc}")
            self._unreachable = True
        return self._cg_cache

    def keys_for(self, cgguid: str, key: str) -> Optional[List[dict]]:
        """Return matching key records, [] when none match, None when unreachable."""
        if self._key_lookup is None or self._unreachable:
            return None
        try:
            return self._key_lookup(cgguid, key) or []
        except Exception as exc:
            logging.warning(f"[dashboard_builder] key lookup failed for {key!r}: {exc}")
            self._unreachable = True
            return None


def _problem(where: str, message: str, severity: str = "error", hint: str = None) -> dict:
    entry = {"severity": severity, "where": where, "message": message}
    if hint:
        entry["hint"] = hint
    return entry


def _coerce_bool(value: Any) -> Optional[bool]:
    """Parse an LLM-supplied flag. Strings such as ``"false"`` stay false."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(int(value))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "on"}:
            return True
        if text in {"false", "no", "0", "off"}:
            return False
    return None


def _resolve_undecorated(
    module: dict, template_id: int, where: str, problems: List[dict]
) -> bool:
    """Frameless only for templates 61 and 105; honor an explicit opt-out."""
    requested = _coerce_bool(module["undecorated"]) if "undecorated" in module else None
    if template_id not in UNDECORATED_TEMPLATE_IDS:
        if requested is True:
            problems.append(
                _problem(
                    where,
                    f"undecorated is only valid for single-value templates 61 and 105, "
                    f"not template {template_id}",
                    severity="warning",
                    hint="Omit undecorated, or switch this panel to template 61 or 105.",
                )
            )
        return False
    if requested is False:
        return False
    return True


def _coerce_option_numbers(value: Any) -> Any:
    """Turn whole-number floats into ints, recursively.

    LLMs routinely emit ``"meter": 0.0``, but meter, statid and topcount are
    indices and counts that Webtrisul uses verbatim, so they must stay integers.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _coerce_option_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_option_numbers(item) for item in value]
    return value


def _coerce_options(raw: Any) -> Tuple[Optional[dict], Optional[str]]:
    if raw is None:
        return {}, None
    if isinstance(raw, dict):
        return _coerce_option_numbers(dict(raw)), None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"options is not valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return None, "options must be a JSON object"
        return _coerce_option_numbers(parsed), None
    return None, f"options must be an object, got {type(raw).__name__}"


def _validate_guid_option(where, opt_name, value, section, resolvers, problems):
    guid = normalize_guid(value)
    if not GUID_RE.match(guid):
        problems.append(
            _problem(
                where,
                f"{opt_name} {value!r} is not a valid GUID",
                hint="Expected the form {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}.",
            )
        )
        return guid

    if section == "counter_groups":
        live = resolvers.counter_groups()
        if live is None:
            problems.append(
                _problem(
                    where,
                    f"could not verify {opt_name} {guid} because the live Trisul "
                    "counter-group-info request failed",
                    hint="Connect to Trisul and call list_all_available_counter_groups, "
                    "then retry with a GUID returned by that request.",
                )
            )
        elif guid not in live:
            problems.append(
                _problem(
                    where,
                    f"{opt_name} {guid} does not exist on this Trisul instance",
                    hint="Call list_all_available_counter_groups and use a GUID from that list, "
                    "or create the counter group first.",
                )
            )
    else:
        known = {normalize_guid(g) for g in load_guid_map().get(section, {}).values()}
        if guid not in known:
            problems.append(
                _problem(
                    where,
                    f"{opt_name} {guid} is not a well-known {section[:-1].replace('_', ' ')}",
                    severity="warning",
                    hint="Confirm the GUID exists on the target Trisul before importing.",
                )
            )
    return guid


def _prefer_readable_key(key: str, matches: List[dict]) -> str:
    """Rewrite host keyformat (C0.A8.0A.0D) to dotted IP (192.168.10.13) when known."""
    if not HOST_KEYFORMAT_RE.match(str(key)):
        return key
    for record in matches:
        if key == record.get("key"):
            readable = record.get("readable")
            if readable and readable != key:
                return str(readable)
    return key


def _validate_key_option(where, cgguid, key, resolvers, problems) -> Optional[str]:
    """Validate a key and return the preferred form to store (readable IP when applicable)."""
    if not key or not cgguid:
        return key

    if str(key).strip().upper() in AGGREGATE_ONLY_KEYS:
        if normalize_guid(cgguid) != GUID_CG_AGGREGATE:
            group_name = (resolvers.counter_groups() or {}).get(normalize_guid(cgguid))
            group_label = f"{cgguid} ({group_name})" if group_name else str(cgguid)
            problems.append(
                _problem(
                    where,
                    f"key {key!r} exists only in the Aggregate counter group, not in {group_label}",
                    hint=(
                        f"Use cgguid {GUID_CG_AGGREGATE} (Aggregate) for this key, or call "
                        f"search_keys on {cgguid} and pick a real key from that group."
                    ),
                )
            )
        return key

    matches = resolvers.keys_for(cgguid, key)
    if matches is None:
        problems.append(
            _problem(
                where,
                f"could not verify key {key!r} because the live Trisul key lookup failed",
                hint="Connect to Trisul and call search_keys for this counter group before retrying.",
            )
        )
        return key
    for record in matches:
        if key in (record.get("key"), record.get("label"), record.get("readable")):
            preferred = _prefer_readable_key(str(key), matches)
            if preferred != key:
                problems.append(
                    _problem(
                        where,
                        f"key {key!r} rewritten to readable form {preferred!r}",
                        severity="warning",
                        hint="Single value modules must use human-readable keys (e.g. 192.168.10.13), not keyformat.",
                    )
                )
            return preferred
    suggestions = [
        record.get("readable") or record.get("label") or record.get("key")
        for record in matches[:5]
        if record.get("readable") or record.get("label") or record.get("key")
    ]
    hint = (
        f"Closest keys in this counter group: {', '.join(suggestions)}"
        if suggestions
        else "Use search_keys on this counter group to find the readable key string."
    )
    problems.append(_problem(where, f"key {key!r} was not found in counter group {cgguid}", hint=hint))
    return key


def _meter_option_name(template: dict) -> Optional[str]:
    """Return the top-level meter option this template requires, if any.

    Templates driven by ``models`` carry the meter inside each series, so they
    have no top-level meter requirement.
    """
    if "models" in template.get("needs", []):
        return None
    option_keys = template.get("option_keys", [])
    for opt_name in METER_OPTION_NAMES:
        if opt_name in option_keys:
            return opt_name
    return None


def _coerce_meter_value(where, opt_name, value, problems) -> Optional[int]:
    """Validate a meter index and return it as an int, or None when invalid."""
    if isinstance(value, bool):
        meter = None
    elif isinstance(value, int):
        meter = value
    elif isinstance(value, float):
        meter = int(value) if value.is_integer() else None
    else:
        text = str(value).strip()
        meter = int(text) if re.fullmatch(r"-?\d+", text) else None

    if meter is None:
        problems.append(
            _problem(
                where,
                f"{opt_name}={value!r} is not a meter index",
                hint="Pass the meter as a whole number, e.g. 0 for Total. "
                "Call get_cginfo_from_countergroup_name to see the counter group's meters.",
            )
        )
        return None
    if meter < 0:
        problems.append(_problem(where, f"{opt_name}={value!r} must be zero or greater"))
        return None
    return meter


def _validate_meter_option(where, template, options, problems) -> None:
    """Require an explicit meter on every module that reads a counter group."""
    opt_name = _meter_option_name(template)
    if opt_name is None:
        return

    value = options.get(opt_name)
    if value is None or (isinstance(value, str) and not value.strip()):
        problems.append(
            _problem(
                where,
                f"option {opt_name!r} (the meter index) is required by template "
                f"{template['template_id']} ({template['name']}) and is missing",
                hint="Meter 0 is Total by convention. Set it explicitly, e.g. "
                f'"{opt_name}": 0, after checking the counter group\'s meters with '
                "get_cginfo_from_countergroup_name.",
            )
        )
        return

    meter = _coerce_meter_value(where, opt_name, value, problems)
    if meter is not None:
        options[opt_name] = meter


def _validate_models(where, template, options, resolvers, problems):
    raw = options.get("models")
    if raw in (None, "", "[]"):
        if "models" in template.get("option_keys", []) and "models" in template.get("needs", []):
            problems.append(_problem(where, "models is required for this template"))
        return

    if isinstance(raw, str):
        try:
            models = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(_problem(where, f"models is not valid JSON: {exc}"))
            return
    else:
        models = raw

    if not isinstance(models, list) or not models:
        problems.append(_problem(where, "models must be a non-empty JSON array"))
        return

    for idx, model in enumerate(models):
        model_where = f"{where}.models[{idx}]"
        if not isinstance(model, dict):
            problems.append(_problem(model_where, "each model must be an object"))
            continue
        cgguid = model.get("cgguid") or model.get("counter_group")
        if not cgguid:
            problems.append(_problem(model_where, "model is missing cgguid"))
            continue
        cgguid = _validate_guid_option(model_where, "cgguid", cgguid, "counter_groups", resolvers, problems)

        meter_value = model.get("meter", model.get("statid"))
        if meter_value is None or (isinstance(meter_value, str) and not meter_value.strip()):
            problems.append(
                _problem(
                    model_where,
                    "model is missing 'meter' (the meter index)",
                    hint="Every series needs an explicit meter, e.g. \"meter\": 0 for Total. "
                    "Call get_cginfo_from_countergroup_name to see the counter group's meters.",
                )
            )
        else:
            meter = _coerce_meter_value(model_where, "meter", meter_value, problems)
            if meter is not None:
                model.pop("statid", None)
                model["meter"] = meter

        if model.get("key"):
            preferred = _validate_key_option(model_where, cgguid, model["key"], resolvers, problems)
            if preferred is not None:
                model["key"] = preferred
        elif template["template_id"] == 54:
            problems.append(
                _problem(
                    model_where,
                    "template 54 charts named keys; each model needs a 'key'",
                    hint="Add a human-readable key (e.g. https[[443]] or TOTALBW). "
                    "Template 54 cannot chart toppers — use 56 for a live toppers list.",
                )
            )
        elif not model.get("topcount") and template["template_id"] in (102, 103):
            problems.append(
                _problem(model_where, "topper models need a topcount", severity="warning")
            )

    options["models"] = json.dumps(models)


def _describe_candidates(presentations: set) -> str:
    candidates = templates_with_presentation(presentations)
    return ", ".join(
        f"{tpl['template_id']} ({tpl['name']})" for tpl in candidates
    ) or "none"


def _validate_intent(where, template, intent, options, problems) -> None:
    """Check the module against the presentation this panel asks for.

    The intent carries this panel's words (the user's own words, or the panel
    the assistant designed for a theme dashboard), which is the only place the
    requested presentation survives into validation. A template whose shape
    contradicts those words is the wrong module, not a detail to mention in
    passing.
    """
    text = str(intent or "").strip()
    if not text:
        problems.append(
            _problem(
                where,
                "intent is required: quote the user's own words for THIS panel",
                hint="e.g. \"intent\": \"https traffic chart\" or, on a theme dashboard, "
                "\"donut of top hosts\". It is used to check that the "
                "template renders the presentation those words ask for.",
            )
        )
        return

    wanted, matched = requested_presentations(text)
    presentation = template.get("presentation")
    if wanted and presentation and presentation not in wanted:
        asked_for = "/".join(sorted(set(matched)))
        problems.append(
            _problem(
                where,
                f"intent {text!r} asks for a {asked_for} but template "
                f"{template['template_id']} ({template['name']}) renders a "
                f"{PRESENTATION_LABELS.get(presentation, presentation)}",
                hint=f"Use one of: {_describe_candidates(wanted)}. If this panel is not the "
                "one the user described that way, put only this panel's own words in 'intent'.",
            )
        )
        return

    # A named-key chart cannot answer "top N over time"; the toppers charts
    # resolve the leaderboard at render time instead.
    if (
        template["template_id"] in (54, 101)
        and _TOPPER_INTENT_RE.search(text.lower())
        and not any(model.get("topcount") for model in _parse_models(options.get("models")))
    ):
        problems.append(
            _problem(
                where,
                f"intent {text!r} asks for top keys over time but template "
                f"{template['template_id']} charts only the keys named in models",
                severity="warning",
                hint=(
                    "Use template 56 (Real time toppers list) on a live dashboard, "
                    "or template 102 on a retro dashboard, so the top N is resolved "
                    "at render time. Keep 54/101 if the user named the exact keys."
                ),
            )
        )

    _flag_realtime_template_mismatch(where, template, text, problems)


def _flag_realtime_template_mismatch(where, template, text, problems, dashboard_realtime=False) -> None:
    """Reject retro templates when this panel or the dashboard is explicitly live."""
    if not dashboard_realtime and not has_explicit_realtime(text):
        return
    tid = template.get("template_id")
    if tid in REALTIME_TEMPLATE_IDS or tid not in RETRO_WHEN_REALTIME:
        return
    problems.append(
        _problem(
            where,
            f"intent/dashboard asks for real time/live traffic but template "
            f"{tid} ({template.get('name')}) is a retro/time-window module",
            hint=f"Switch this panel to {REALTIME_REPLACEMENT_HINT.get(tid, '54, 56, or 105')}.",
        )
    )


def _empty_option_value(opt_name: str) -> Any:
    """Placeholder for an option_key the LLM omitted entirely."""
    if opt_name in JSON_STRING_OPTIONS:
        return "[]"
    if opt_name in ZERO_WHEN_UNSET_OPTIONS:
        return 0
    return None


def _is_unset(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _fill_option_keys(template: dict, options: dict) -> dict:
    """Ensure every catalog option_key is present in the written options.

    Webtrisul templates often read option keys with ``.strip`` / ``JSON.parse``
    and crash when the key is absent. Fill missing keys from the template
    defaults, otherwise with null (or "[]" for JSON-text model fields).
    Unknown keys outside option_keys are dropped.

    Counter-group GUID options are never filled from catalog defaults — those
    values are installation-specific and must come from a live lookup.
    """
    defaults = template.get("defaults") or {}
    filled: dict = {}
    for opt_name in template.get("option_keys", []):
        if opt_name in ZERO_WHEN_UNSET_OPTIONS and _is_unset(options.get(opt_name)):
            filled[opt_name] = 0
        elif opt_name in options and options[opt_name] is not None:
            filled[opt_name] = options[opt_name]
        elif opt_name in ("cgguid", "counter_group", "key", "label"):
            # Never inject static catalog identity values; leave empty so live
            # validation / needs checks fail loudly when the LLM omitted them.
            filled[opt_name] = None
        elif opt_name in defaults:
            filled[opt_name] = deepcopy(defaults[opt_name])
        else:
            filled[opt_name] = _empty_option_value(opt_name)
    return _coerce_option_numbers(filled)


def _stringify_json_options(options: dict) -> None:
    """Store models/refmodel as JSON text, the form Webtrisul JSON.parses."""
    for opt_name in JSON_STRING_OPTIONS:
        value = options.get(opt_name)
        if isinstance(value, (list, dict)):
            options[opt_name] = json.dumps(value)


def _replace_empty_strings(value: Any) -> Any:
    """Recursively represent empty JSON fields as null, never as an empty string."""
    if value == "":
        return None
    if isinstance(value, dict):
        return {key: _replace_empty_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_empty_strings(item) for item in value]
    return value


def _serialize_options(options: dict) -> str:
    """Serialize module options without empty strings, including JSON-text fields."""
    sanitized = _replace_empty_strings(options)
    for opt_name in JSON_STRING_OPTIONS:
        raw = sanitized.get(opt_name)
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        sanitized[opt_name] = json.dumps(_replace_empty_strings(parsed))
    return json.dumps(sanitized)


def validate_module(module: dict, index: int, resolvers: Resolvers) -> Tuple[Optional[dict], List[dict]]:
    """Validate one module spec. Returns (normalized_module, problems)."""
    problems: List[dict] = []
    where = f"modules[{index}]"

    if not isinstance(module, dict):
        return None, [_problem(where, "each module must be an object")]

    template = get_template(module.get("template_id"))
    if template is None:
        return None, [
            _problem(
                where,
                f"unknown template_id {module.get('template_id')!r}",
                hint="Call list_dashboard_module_types to see the available module types.",
            )
        ]

    name = str(module.get("name") or "").strip()
    if not name:
        problems.append(_problem(where, "name is required and becomes the module's panel title"))

    options, options_error = _coerce_options(module.get("options"))
    if options_error:
        return None, [_problem(where, options_error)]

    allowed = set(template.get("option_keys", []))
    # An option the template cannot accept almost always means the template is
    # wrong for what was asked, so name the templates that do accept it rather
    # than dropping the option and rendering a panel nobody asked for.
    for opt_name in sorted(set(options) - allowed):
        accepted_by = ", ".join(
            f"{tpl['template_id']} ({tpl['name']})"
            for tpl in load_catalog()["templates"]
            if opt_name in tpl.get("option_keys", [])
        )
        hint = f"Accepted options for this template: {', '.join(sorted(allowed))}."
        if accepted_by:
            hint += (
                f" If the panel really needs {opt_name!r}, switch to a template that accepts "
                f"it: {accepted_by}."
            )
        problems.append(
            _problem(
                where,
                f"option {opt_name!r} is not accepted by template {template['template_id']} "
                f"({template['name']})",
                hint=hint,
            )
        )

    # Every option_key must appear in the written JSON (empty string is fine).
    options = _fill_option_keys(template, options)
    if "label" in options and not str(options.get("label") or "").strip() and name:
        options["label"] = name

    for opt_name, valid_values in template.get("enums", {}).items():
        value = options.get(opt_name)
        if value is None or value == "":
            continue
        text = str(value)
        match = next((v for v in valid_values if v.lower() == text.lower()), None)
        if match is None:
            problems.append(
                _problem(
                    where,
                    f"{opt_name}={value!r} is not valid for this template",
                    hint=f"Allowed values: {', '.join(valid_values)}",
                )
            )
        else:
            options[opt_name] = match

    for opt_name in template.get("needs", []):
        # models is validated in depth by _validate_models.
        if opt_name != "models" and not options.get(opt_name):
            problems.append(
                _problem(where, f"option {opt_name!r} is required by template {template['template_id']}")
            )

    module_cgguid = None
    for opt_name, section in GUID_OPTIONS.items():
        if options.get(opt_name):
            resolved = _validate_guid_option(where, opt_name, options[opt_name], section, resolvers, problems)
            options[opt_name] = resolved
            if section == "counter_groups":
                module_cgguid = resolved

    for opt_name in KEY_OPTIONS:
        if options.get(opt_name):
            preferred = _validate_key_option(
                where, module_cgguid, str(options[opt_name]), resolvers, problems
            )
            if preferred is not None:
                options[opt_name] = preferred

    _validate_meter_option(where, template, options, problems)
    _validate_models(where, template, options, resolvers, problems)
    _validate_intent(where, template, module.get("intent"), options, problems)
    _stringify_json_options(options)

    width = module.get("width", template.get("recommended_width", 6))
    try:
        width = int(width)
    except (TypeError, ValueError):
        problems.append(_problem(where, f"width {width!r} must be an integer between 1 and 12"))
        width = template.get("recommended_width", 6)
    if not 1 <= width <= 12:
        problems.append(_problem(where, f"width {width} must be between 1 and 12"))
        width = max(1, min(12, width))

    normalized = {
        "template_id": template["template_id"],
        "template_name": template["name"],
        "presentation": template.get("presentation"),
        "intent": str(module.get("intent") or "").strip(),
        "name": name,
        "description": module.get("description"),
        "options": options,
        "width": width,
        "width_locked": bool(module.get("width_locked", False)),
        "undecorated": _resolve_undecorated(module, template["template_id"], where, problems),
    }
    return normalized, problems


def pack_rows(modules: List[dict]) -> List[List[dict]]:
    """Group modules into rows of at most 12 columns, in import order."""
    rows: List[List[dict]] = []
    row: List[dict] = []
    used = 0
    for module in modules:
        if row and used + module["width"] > 12:
            rows.append(row)
            row, used = [], 0
        row.append(module)
        used += module["width"]
    if row:
        rows.append(row)
    return rows


def _fill_layout_rows(modules: List[dict]) -> None:
    """Grow each row to the full 12 columns so no grid space is left blank.

    A width is only a relative sizing hint: a row of w6 + w4 leaves two dead
    columns, so the two panels are widened to w7 + w5 keeping their ratio.
    Modules flagged ``width_locked`` keep the exact width that was asked for,
    and the spare columns go to the rest of the row instead.
    """
    for row in pack_rows(modules):
        remaining = 12 - sum(module["width"] for module in row)
        flexible = [module for module in row if not module["width_locked"]]
        if remaining <= 0 or not flexible:
            continue

        # Largest-remainder apportionment keeps the row's width ratios intact
        # while every module still ends up an integer number of columns.
        total = sum(module["width"] for module in flexible)
        shares = [(remaining * module["width"] / total, module) for module in flexible]
        for share, module in shares:
            module["width"] += int(share)

        spare = remaining - sum(int(share) for share, _ in shares)
        shares.sort(key=lambda item: item[0] - int(item[0]), reverse=True)
        for _, module in shares[:spare]:
            module["width"] += 1


def validate_modules(
    modules: Any,
    resolvers: Resolvers,
    dashboard: Optional[dict] = None,
) -> Tuple[List[dict], List[dict]]:
    if not isinstance(modules, list) or not modules:
        return [], [_problem("modules", "at least one module is required")]

    normalized: List[dict] = []
    problems: List[dict] = []
    for index, module in enumerate(modules):
        result, module_problems = validate_module(module, index, resolvers)
        problems.extend(module_problems)
        if result is not None:
            normalized.append(result)

    dashboard_text = " ".join(
        str((dashboard or {}).get(field) or "")
        for field in ("name", "description")
    )
    if has_explicit_realtime(dashboard_text):
        for index, module in enumerate(normalized):
            tid = module["template_id"]
            if tid in RETRO_WHEN_REALTIME:
                problems.append(
                    _problem(
                        f"modules[{index}]",
                        f"dashboard is real time/live but template {tid} "
                        f"({module.get('template_name')}) is a retro/time-window module",
                        hint=(
                            "Switch this panel to "
                            f"{REALTIME_REPLACEMENT_HINT.get(tid, '54, 56, or 105')}."
                        ),
                    )
                )

    _fill_layout_rows(normalized)
    return normalized, problems


def validate_dashboard(dashboard: Any) -> Tuple[dict, List[dict]]:
    problems: List[dict] = []
    if not isinstance(dashboard, dict):
        return {}, [_problem("dashboard", "dashboard must be an object")]

    name = str(dashboard.get("name") or "").strip()
    if not name:
        problems.append(_problem("dashboard", "name is required"))

    # WebtrisulDashboard validates presence of description.
    description = str(dashboard.get("description") or "").strip()
    if not description:
        description = f"Dashboard generated for {name}" if name else ""
        if not description:
            problems.append(_problem("dashboard", "description is required"))

    key = str(dashboard.get("key") or "").strip() or f"dashboard{slugify_key(name)}"
    if not DASHBOARD_KEY_RE.match(key):
        cleaned = slugify_key(key)
        problems.append(
            _problem(
                "dashboard",
                f"key {key!r} must be alphanumeric only; using {cleaned!r} instead",
                severity="warning",
            )
        )
        key = cleaned

    return {
        "name": name,
        "description": description,
        "key": key,
        "package_name": dashboard.get("package_name") or name,
        "package_description": dashboard.get("package_description") or description,
        "author": dashboard.get("author") or "trisul",
        "version": str(dashboard.get("version") or "1.0"),
    }, problems


def build_package(
    dashboard: dict,
    modules: List[dict],
    base_module_id: int = DEFAULT_BASE_MODULE_ID,
    dashboard_id: int = None,
) -> list:
    """Assemble the importable package.

    Module ids are assigned in list order and ``pos_flexi`` is written in that
    same order. The importer renumbers modules sequentially in array order while
    ``offset_modids`` renumbers by order of first appearance in ``pos_flexi``, so
    the two orders must agree or every panel lands in the wrong slot.
    """
    dashboard_id = DEFAULT_DASHBOARD_ID if dashboard_id is None else dashboard_id

    module_rows = []
    positions = []
    undecorated = []

    for offset, module in enumerate(modules):
        module_id = base_module_id + offset
        module_rows.append(
            {
                "id": module_id,
                "webtrisul_addon_package_id": 1,
                "webtrisul_addon_package_mod_id": 0,
                "webtrisul_module_template_id": module["template_id"],
                "name": module["name"],
                "description": module.get("description"),
                "options": _serialize_options(module["options"]),
                "stretch_to_fit": None,
                "maintain_aspect_ratio": None,
                "show_decoration": None,
            }
        )
        positions.append(f"{module_id}:w{module['width']}")
        if module.get("undecorated") and module.get("template_id") in UNDECORATED_TEMPLATE_IDS:
            undecorated.append(str(module_id))

    dashboard_row = {
        "id": dashboard_id,
        "webtrisul_addon_package_id": 1,
        "trisul_web_user_id": 2,
        "key": dashboard["key"],
        "name": dashboard["name"],
        "description": dashboard["description"],
        "moduri": MODURI,
        "axupdateuri": AXUPDATEURI,
        "collapsedmods": "",
        "undecoratedmods": ",".join(undecorated),
        "pos_head": None,
        "pos_left": None,
        "pos_right": None,
        "pos_foot": None,
        "pos_top1": None,
        "pos_top2": None,
        "pos_mid3": None,
        "pos_bot2": None,
        "pos_bot1": None,
        "pos_mid4": None,
        "pos_flexi": ",".join(positions),
        "pos_reserved2": None,
        "pos_reserved3": None,
        "dashboard_type": None,
    }

    package_row = {
        "name": dashboard["package_name"],
        "description": dashboard["package_description"],
        "author": dashboard["author"],
        "version": dashboard["version"],
    }

    return _replace_empty_strings([[package_row], [dashboard_row], module_rows])


def render_preview(dashboard: dict, modules: List[dict]) -> str:
    """ASCII sketch of the 12-column layout, row by row."""
    lines = [
        f"Dashboard: {dashboard['name']}  (key: {dashboard['key']})",
        f"           {dashboard['description']}",
        "",
        "Layout (12-column grid, top to bottom):",
    ]

    for row_index, row in enumerate(pack_rows(modules), start=1):
        cells = " | ".join(
            f"{module['name']} [w{module['width']}]"
            + (
                " (no frame)"
                if module.get("undecorated")
                and module.get("template_id") in UNDECORATED_TEMPLATE_IDS
                else ""
            )
            for module in row
        )
        lines.append(f"  Row {row_index}: {cells}")

    lines.append("")
    lines.append("Modules in import order:")
    for index, module in enumerate(modules, start=1):
        summary = _summarize_options(module)
        shape = PRESENTATION_LABELS.get(module.get("presentation"), "panel")
        lines.append(
            f"  {index}. {module['name']} - renders: {shape} - {module['template_name']} "
            f"(template {module['template_id']}, w{module['width']}){summary}"
        )
        if module.get("intent"):
            lines.append(f"       asked for: {module['intent']}")
    return "\n".join(lines)


def _summarize_options(module: dict) -> str:
    options = module.get("options", {})
    bits = []

    series_list = _parse_models(options.get("models"))
    for series in series_list:
        label = series.get("label") or series.get("key") or f"top {series.get('topcount')}"
        bits.append(f"series {label} meter {series.get('meter')} from {series.get('cgguid')}")

    for opt_name in (
        "cgguid", "counter_group", "alert_group", "rguid", "key",
        "statid", "meter", "topcount", "surface", "url",
    ):
        # For chart templates these are stale UI leftovers; models is the real source.
        if series_list and opt_name in ("cgguid", "key", "statid", "meter"):
            continue
        value = options.get(opt_name)
        # A meter of 0 is meaningful, so test for presence rather than truthiness.
        if value or (opt_name in METER_OPTION_NAMES and value is not None):
            bits.append(f"{opt_name}={value}")
    return f" | {', '.join(bits)}" if bits else ""


def _parse_models(raw: Any) -> List[dict]:
    if raw in (None, "", "[]"):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [model for model in raw if isinstance(model, dict) and model] if isinstance(raw, list) else []
