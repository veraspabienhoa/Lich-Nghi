"""Source hooks for Phase 13 PostgreSQL-primary configuration."""
from __future__ import annotations

import ast
import re


MARKER = "_PHASE13_CONFIGURATION_PATCH_V1 = True"
TARGETS = {
    "load_admin_menu_order": "_phase13_legacy_load_admin_menu_order",
    "save_admin_menu_order": "_phase13_legacy_save_admin_menu_order",
    "load_table_layouts": "_phase13_legacy_load_table_layouts",
    "save_table_layout_config": "_phase13_legacy_save_table_layout_config",
    "_load_payroll_config_rows_cached": "_phase13_legacy_load_payroll_config_rows_cached",
    "set_payroll_letan_enabled": "_phase13_legacy_set_payroll_letan_enabled",
    "set_payroll_default_amounts": "_phase13_legacy_set_payroll_default_amounts",
    "set_leader_responsibility_allowance": "_phase13_legacy_set_leader_responsibility_allowance",
    "_write_payroll_employee_overrides": "_phase13_legacy_write_payroll_employee_overrides",
}


HELPER_BLOCK = r'''
_PHASE13_CONFIGURATION_PATCH_V1 = True

_PHASE13_ADMIN_MENU_KEY = "admin_menu_order"
_PHASE13_TABLE_LAYOUT_KEY = "table_layouts"
_PHASE13_PAYROLL_CONFIG_KEY = "payroll_config_rows"


def _phase13_current_user():
    try:
        return str(st.session_state.get("current_user", "") or "")
    except Exception:
        return ""


def _phase13_read_config(key, source_loader, default):
    if vpg is not None:
        fn = getattr(vpg, "phase13_read_config", None)
        if callable(fn):
            return fn(key, source_loader, default=default)
    try:
        result = source_loader()
    except Exception as exc:
        return default, f"{type(exc).__name__}: {exc}"
    if isinstance(result, tuple) and len(result) >= 2:
        return result[0], str(result[1] or "")
    return result, ""


def _phase13_commit_config(key, value, mirror_fn, updated_by="", confirm_fn=None):
    if vpg is not None:
        fn = getattr(vpg, "phase13_commit_config", None)
        if callable(fn):
            return fn(
                key,
                value,
                mirror_fn,
                updated_by=updated_by,
                confirm_fn=confirm_fn,
            )
    return mirror_fn()


def _phase13_admin_menu_source():
    try:
        _phase13_legacy_load_admin_menu_order.clear()
    except Exception:
        pass
    return _phase13_legacy_load_admin_menu_order()


def _phase13_layout_source():
    try:
        _phase13_legacy_load_table_layouts.clear()
    except Exception:
        pass
    return _phase13_legacy_load_table_layouts()


def _phase13_payroll_source():
    try:
        _phase13_legacy_load_payroll_config_rows_cached.clear()
    except Exception:
        pass
    return _phase13_legacy_load_payroll_config_rows_cached()


def _phase13_clean_menu_values(values):
    out, seen = [], set()
    for item in values or []:
        item = str(item).strip()
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    valid = [x for x in out if x in CANONICAL_MENU_ORDER_V92698]
    return valid + [x for x in CANONICAL_MENU_ORDER_V92698 if x not in valid]


def _phase13_next_admin_menu(current, order, device=None):
    current = current if isinstance(current, dict) else {}
    pair = {
        "desktop": _phase13_clean_menu_values(current.get("desktop", [])),
        "mobile": _phase13_clean_menu_values(current.get("mobile", [])),
    }
    if isinstance(order, dict):
        pair["desktop"] = _phase13_clean_menu_values(order.get("desktop", pair["desktop"]))
        pair["mobile"] = _phase13_clean_menu_values(order.get("mobile", pair["mobile"]))
    else:
        target = str(device or _ui_runtime_device())
        if target not in {"desktop", "mobile"}:
            target = "desktop"
        pair[target] = _phase13_clean_menu_values(order)
    return pair


def _phase13_clone_json(value, fallback):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return fallback


def _phase13_clamp_widths(values):
    out = {}
    for key, value in (values or {}).items():
        out[str(key)] = max(50, min(800, int(float(value))))
    return out


def _phase13_next_layouts(current, table_key, order, widths, username, visual=None, device=None):
    result = _phase13_clone_json(current if isinstance(current, dict) else {}, {})
    table_key = str(table_key)
    cfg = result.get(table_key, {}) if isinstance(result.get(table_key, {}), dict) else {}
    devices_old = cfg.get("devices", {}) if isinstance(cfg.get("devices", {}), dict) else {}

    pair = {}
    for d in ("desktop", "mobile"):
        old = devices_old.get(d, {}) if isinstance(devices_old.get(d, {}), dict) else {}
        pair[d] = {
            "order": list(old.get("order", [])) if isinstance(old.get("order", []), list) else [],
            "widths": dict(old.get("widths", {})) if isinstance(old.get("widths", {}), dict) else {},
            "visual": dict(old.get("visual", {})) if isinstance(old.get("visual", {}), dict) else {},
        }

    pair_order = isinstance(order, dict) and any(k in order for k in ("desktop", "mobile"))
    pair_widths = isinstance(widths, dict) and any(k in widths for k in ("desktop", "mobile"))
    pair_visual = isinstance(visual, dict) and any(k in visual for k in ("desktop", "mobile"))

    if pair_order or pair_widths or pair_visual:
        for d in ("desktop", "mobile"):
            if pair_order and isinstance(order.get(d, []), list):
                pair[d]["order"] = [str(x) for x in order[d]]
            if pair_widths and isinstance(widths.get(d, {}), dict):
                pair[d]["widths"] = _phase13_clamp_widths(widths[d])
            if pair_visual and isinstance(visual.get(d, {}), dict):
                pair[d]["visual"] = _phase13_clone_json(visual[d], {})
    else:
        target = str(device or _ui_runtime_device())
        if target not in {"desktop", "mobile"}:
            target = "desktop"
        pair[target]["order"] = [str(x) for x in (order or [])]
        pair[target]["widths"] = _phase13_clamp_widths(widths or {})
        if visual is not None:
            pair[target]["visual"] = _phase13_clone_json(visual if isinstance(visual, dict) else {}, {})

    now = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")
    result[table_key] = {
        "row": cfg.get("row"),
        "devices": {
            "desktop": {
                "order": list(pair["desktop"]["order"]),
                "widths": dict(pair["desktop"]["widths"]),
                "visual": dict(pair["desktop"]["visual"]),
            },
            "mobile": {
                "order": list(pair["mobile"]["order"]),
                "widths": dict(pair["mobile"]["widths"]),
                "visual": dict(pair["mobile"]["visual"]),
            },
        },
        "updated_at": now,
        "updated_by": str(username),
    }
    return result


def _phase13_default_payroll_rows():
    return [
        ["Key", "Value"],
        ["letan_payroll_access", "0"],
        ["default_living_expense", "150000"],
        ["default_locker_support", "80000"],
        ["employee_payroll_overrides_json", "{}"],
        ["leader_responsibility_allowance", "0"],
    ]


def _phase13_payroll_rows_set(rows, updates):
    src = rows if isinstance(rows, list) and rows else _phase13_default_payroll_rows()
    out = []
    for row in src:
        if isinstance(row, (list, tuple)):
            out.append([str(x) for x in row])
        else:
            out.append([str(row)])
    if not out:
        out = _phase13_default_payroll_rows()
    if not out[0] or str(out[0][0]).strip() != "Key":
        out.insert(0, ["Key", "Value"])

    key_rows = {}
    for idx, row in enumerate(out[1:], start=1):
        if row and str(row[0]).strip():
            key_rows[str(row[0]).strip()] = idx

    for key, value in updates:
        key = str(key)
        value = str(value)
        idx = key_rows.get(key)
        if idx is None:
            out.append([key, value])
            key_rows[key] = len(out) - 1
        else:
            while len(out[idx]) < 2:
                out[idx].append("")
            out[idx][0] = key
            out[idx][1] = value
    return out
'''.strip("\n")


WRAPPERS = {
    "_phase13_legacy_load_admin_menu_order": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_admin_menu_order():
    canonical = {
        "desktop": list(CANONICAL_MENU_ORDER_V92698),
        "mobile": list(CANONICAL_MENU_ORDER_V92698),
    }
    value, err = _phase13_read_config(
        _PHASE13_ADMIN_MENU_KEY,
        _phase13_admin_menu_source,
        canonical,
    )
    if not isinstance(value, dict):
        value = canonical
    return {
        "desktop": _phase13_clean_menu_values(value.get("desktop", [])),
        "mobile": _phase13_clean_menu_values(value.get("mobile", [])),
    }, err
'''.strip("\n"),

    "_phase13_legacy_save_admin_menu_order": r'''
def save_admin_menu_order(order, username, device=None):
    current, read_err = load_admin_menu_order()
    if read_err and vpg is None:
        return _phase13_legacy_save_admin_menu_order(order, username, device=device)
    desired = _phase13_next_admin_menu(current, order, device=device)

    def _p13_mirror_admin_menu():
        return _phase13_legacy_save_admin_menu_order(order, username, device=device)

    result = _phase13_commit_config(
        _PHASE13_ADMIN_MENU_KEY,
        desired,
        _p13_mirror_admin_menu,
        updated_by=str(username or ""),
        confirm_fn=_phase13_admin_menu_source,
    )
    try:
        load_admin_menu_order.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase13_legacy_load_table_layouts": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_table_layouts():
    value, err = _phase13_read_config(
        _PHASE13_TABLE_LAYOUT_KEY,
        _phase13_layout_source,
        {},
    )
    return value if isinstance(value, dict) else {}, err
'''.strip("\n"),

    "_phase13_legacy_save_table_layout_config": r'''
def save_table_layout_config(table_key, order, widths, username, visual=None, device=None):
    try:
        current, read_err = load_table_layouts()
        if read_err and vpg is None:
            return _phase13_legacy_save_table_layout_config(
                table_key, order, widths, username, visual=visual, device=device
            )
        desired = _phase13_next_layouts(
            current,
            table_key,
            order,
            widths,
            username,
            visual=visual,
            device=device,
        )
    except Exception as exc:
        return False, f"Lỗi lưu giao diện tùy chỉnh: {exc}"

    def _p13_mirror_layout():
        return _phase13_legacy_save_table_layout_config(
            table_key, order, widths, username, visual=visual, device=device
        )

    result = _phase13_commit_config(
        _PHASE13_TABLE_LAYOUT_KEY,
        desired,
        _p13_mirror_layout,
        updated_by=str(username or ""),
        confirm_fn=_phase13_layout_source,
    )
    try:
        load_table_layouts.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase13_legacy_load_payroll_config_rows_cached": r'''
@st.cache_data(ttl=300, show_spinner=False)
def _load_payroll_config_rows_cached():
    value, err = _phase13_read_config(
        _PHASE13_PAYROLL_CONFIG_KEY,
        _phase13_payroll_source,
        [],
    )
    return value if isinstance(value, list) else [], err
'''.strip("\n"),

    "_phase13_legacy_set_payroll_letan_enabled": r'''
def set_payroll_letan_enabled(enabled):
    rows, read_err = _load_payroll_config_rows_cached()
    if read_err:
        return False, read_err
    desired = _phase13_payroll_rows_set(
        rows,
        [("letan_payroll_access", "1" if enabled else "0")],
    )

    def _p13_mirror_payroll_letan():
        return _phase13_legacy_set_payroll_letan_enabled(enabled)

    result = _phase13_commit_config(
        _PHASE13_PAYROLL_CONFIG_KEY,
        desired,
        _p13_mirror_payroll_letan,
        updated_by=_phase13_current_user(),
        confirm_fn=_phase13_payroll_source,
    )
    _clear_payroll_config_cache()
    return result
'''.strip("\n"),

    "_phase13_legacy_set_payroll_default_amounts": r'''
def set_payroll_default_amounts(living_expense, locker_support):
    rows, read_err = _load_payroll_config_rows_cached()
    if read_err:
        return False, read_err
    living = int(round(_money_to_float(living_expense)))
    locker = int(round(_money_to_float(locker_support)))
    desired = _phase13_payroll_rows_set(
        rows,
        [
            ("default_living_expense", living),
            ("default_locker_support", locker),
        ],
    )

    def _p13_mirror_payroll_defaults():
        return _phase13_legacy_set_payroll_default_amounts(living_expense, locker_support)

    result = _phase13_commit_config(
        _PHASE13_PAYROLL_CONFIG_KEY,
        desired,
        _p13_mirror_payroll_defaults,
        updated_by=_phase13_current_user(),
        confirm_fn=_phase13_payroll_source,
    )
    _clear_payroll_config_cache()
    return result
'''.strip("\n"),

    "_phase13_legacy_set_leader_responsibility_allowance": r'''
def set_leader_responsibility_allowance(amount):
    rows, read_err = _load_payroll_config_rows_cached()
    if read_err:
        return False, read_err
    value = int(round(_money_to_float(amount)))
    desired = _phase13_payroll_rows_set(
        rows,
        [("leader_responsibility_allowance", value)],
    )

    def _p13_mirror_leader_allowance():
        return _phase13_legacy_set_leader_responsibility_allowance(amount)

    result = _phase13_commit_config(
        _PHASE13_PAYROLL_CONFIG_KEY,
        desired,
        _p13_mirror_leader_allowance,
        updated_by=_phase13_current_user(),
        confirm_fn=_phase13_payroll_source,
    )
    _clear_payroll_config_cache()
    return result
'''.strip("\n"),

    "_phase13_legacy_write_payroll_employee_overrides": r'''
def _write_payroll_employee_overrides(overrides):
    rows, read_err = _load_payroll_config_rows_cached()
    if read_err:
        return False, read_err
    payload = json.dumps(overrides or {}, ensure_ascii=False, separators=(",", ":"))
    desired = _phase13_payroll_rows_set(
        rows,
        [("employee_payroll_overrides_json", payload)],
    )

    def _p13_mirror_payroll_overrides():
        return _phase13_legacy_write_payroll_employee_overrides(overrides)

    result = _phase13_commit_config(
        _PHASE13_PAYROLL_CONFIG_KEY,
        desired,
        _p13_mirror_payroll_overrides,
        updated_by=_phase13_current_user(),
        confirm_fn=_phase13_payroll_source,
    )
    _clear_payroll_config_cache()
    return result
'''.strip("\n"),
}


def _rename_top_level_function(source, old, new):
    pattern = re.compile(rf"(?m)^def {re.escape(old)}\(")
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        return source, len(matches)
    return pattern.sub(f"def {new}(", source, count=1), 1


def apply(source):
    warnings = []
    if MARKER in source:
        return source, warnings

    renamed = source
    for old, new in TARGETS.items():
        renamed, count = _rename_top_level_function(renamed, old, new)
        if count != 1:
            warnings.append(f"phase13_rename_{old}:{count}")

    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        warnings.append(f"phase13_ast:{type(exc).__name__}")
        return source, warnings

    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [name for name in TARGETS.values() if name not in nodes]
    if missing:
        warnings.extend(f"phase13_node_{name}:0" for name in missing)
        return source, warnings

    lines = renamed.splitlines(keepends=True)
    insertions = []

    first_node = min((nodes[name] for name in TARGETS.values()), key=lambda node: node.lineno)
    first_start = int(first_node.lineno)
    if getattr(first_node, "decorator_list", None):
        first_start = min(
            first_start,
            *(int(decorator.lineno) for decorator in first_node.decorator_list),
        )
    insertions.append((max(0, first_start - 1), HELPER_BLOCK + "\n\n"))

    for legacy, wrapper in WRAPPERS.items():
        node = nodes[legacy]
        insertions.append(
            (int(getattr(node, "end_lineno", node.lineno)), "\n\n" + wrapper + "\n")
        )

    for index, block in sorted(insertions, key=lambda item: item[0], reverse=True):
        lines.insert(index, block)

    patched = "".join(lines)
    try:
        ast.parse(patched)
    except Exception as exc:
        warnings.append(f"phase13_patched_ast:{type(exc).__name__}")
        return source, warnings

    return patched, warnings
