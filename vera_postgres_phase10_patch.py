"""Source hooks for Phase 10 PostgreSQL-primary control settings."""
from __future__ import annotations

import ast
import re


MARKER = "_PHASE10_CONTROLS_PATCH_V1 = True"

TARGETS = {
    "load_registration_role_locks": "_phase10_legacy_load_registration_role_locks",
    "set_registration_role_lock": "_phase10_legacy_set_registration_role_lock",
    "load_auto_penalty_config": "_phase10_legacy_load_auto_penalty_config",
    "set_auto_penalty_paused": "_phase10_legacy_set_auto_penalty_paused",
    "load_midshift_deadline_config": "_phase10_legacy_load_midshift_deadline_config",
    "save_midshift_deadline_config": "_phase10_legacy_save_midshift_deadline_config",
}


HELPER_BLOCK = r'''
_PHASE10_CONTROLS_PATCH_V1 = True


def _phase10_read_control(setting_key, source_loader, default=None):
    if vpg is not None:
        fn = getattr(vpg, "phase10_read_setting", None)
        if callable(fn):
            return fn(setting_key, source_loader, default=default)
    try:
        return source_loader()
    except Exception:
        return default


def _phase10_commit_control(
    setting_key,
    value,
    mirror_fn,
    updated_by="",
    operation="update",
    confirm_fn=None,
):
    if vpg is not None:
        fn = getattr(vpg, "phase10_commit_setting", None)
        if callable(fn):
            return fn(
                setting_key,
                value,
                mirror_fn,
                updated_by=updated_by,
                operation=operation,
                confirm_fn=confirm_fn,
            )
    return mirror_fn()
'''.strip("\n")


WRAPPERS = {
    "_phase10_legacy_load_registration_role_locks": r'''
@st.cache_data(ttl=15, show_spinner=False)
def load_registration_role_locks():
    default = {role: False for role in REGISTRATION_LOCK_ROLES}
    raw = _phase10_read_control(
        "registration_role_locks",
        _phase10_legacy_load_registration_role_locks,
        default=default,
    )
    if not isinstance(raw, dict):
        return default
    out = default.copy()
    for role in REGISTRATION_LOCK_ROLES:
        out[role] = bool(raw.get(role, False))
    return out
'''.strip("\n"),

    "_phase10_legacy_set_registration_role_lock": r'''
def set_registration_role_lock(role, locked, actor=""):
    role = str(role or "").strip().lower()
    if role == "admin":
        return False, "Admin luôn được mở quyền đăng ký và không thể bị khóa."
    if role not in REGISTRATION_LOCK_ROLES:
        return False, f"Vai trò không hợp lệ: {role}"

    current = load_registration_role_locks()
    new_value = {r: bool(current.get(r, False)) for r in REGISTRATION_LOCK_ROLES}
    new_value[role] = bool(locked)

    def _p10_mirror_registration():
        result = _phase10_legacy_set_registration_role_lock(role, locked, actor)
        try:
            _phase10_legacy_load_registration_role_locks.clear()
        except Exception:
            pass
        return result

    def _p10_confirm_registration():
        try:
            _phase10_legacy_load_registration_role_locks.clear()
        except Exception:
            pass
        return _phase10_legacy_load_registration_role_locks()

    result = _phase10_commit_control(
        "registration_role_locks",
        new_value,
        _p10_mirror_registration,
        updated_by=str(actor or ""),
        operation=f"registration_role_lock:{role}",
        confirm_fn=_p10_confirm_registration,
    )
    try:
        load_registration_role_locks.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase10_legacy_load_auto_penalty_config": r'''
def load_auto_penalty_config():
    default = {
        "paused": False,
        "status": AUTO_PENALTY_RUNNING,
        "threshold_minutes": AUTO_PENALTY_MINUTES,
        "updated_date": "",
        "updated_time": "",
        "updated_by": "",
        "error": "",
    }
    raw = _phase10_read_control(
        "auto_penalty_config",
        _phase10_legacy_load_auto_penalty_config,
        default=default,
    )
    if not isinstance(raw, dict):
        return default
    out = default.copy()
    out.update(raw)
    out["paused"] = bool(out.get("paused", False))
    out["status"] = AUTO_PENALTY_PAUSED if out["paused"] else AUTO_PENALTY_RUNNING
    try:
        threshold = int(float(out.get("threshold_minutes", AUTO_PENALTY_MINUTES)))
    except Exception:
        threshold = AUTO_PENALTY_MINUTES
    out["threshold_minutes"] = max(AUTO_PENALTY_MINUTES, threshold)
    out["error"] = str(out.get("error", "") or "")
    return out
'''.strip("\n"),

    "_phase10_legacy_set_auto_penalty_paused": r'''
def set_auto_penalty_paused(paused, updated_by):
    paused = bool(paused)
    current = load_auto_penalty_config()
    now = datetime.now(VN_TZ)
    new_value = dict(current) if isinstance(current, dict) else {}
    try:
        _threshold = int(float(new_value.get("threshold_minutes", AUTO_PENALTY_MINUTES) or AUTO_PENALTY_MINUTES))
    except Exception:
        _threshold = AUTO_PENALTY_MINUTES
    new_value.update({
        "paused": paused,
        "status": AUTO_PENALTY_PAUSED if paused else AUTO_PENALTY_RUNNING,
        "threshold_minutes": max(AUTO_PENALTY_MINUTES, _threshold),
        "updated_date": now.strftime("%d/%m/%Y"),
        "updated_time": now.strftime("%H:%M:%S"),
        "updated_by": str(updated_by or ""),
        "error": "",
    })

    def _p10_mirror_auto_penalty():
        return _phase10_legacy_set_auto_penalty_paused(paused, updated_by)

    def _p10_confirm_auto_penalty():
        return _phase10_legacy_load_auto_penalty_config()

    return _phase10_commit_control(
        "auto_penalty_config",
        new_value,
        _p10_mirror_auto_penalty,
        updated_by=str(updated_by or ""),
        operation="auto_penalty_pause" if paused else "auto_penalty_resume",
        confirm_fn=_p10_confirm_auto_penalty,
    )
'''.strip("\n"),

    "_phase10_legacy_load_midshift_deadline_config": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_midshift_deadline_config():
    default = {
        "return_deadline": MIDSHIFT_RETURN_DEADLINE_DEFAULT,
        "late_threshold_minutes": MIDSHIFT_LATE_THRESHOLD_DEFAULT,
        "updated_date": "",
        "updated_time": "",
        "updated_by": "",
    }
    raw = _phase10_read_control(
        "midshift_deadline_config",
        _phase10_legacy_load_midshift_deadline_config,
        default=default,
    )
    if not isinstance(raw, dict):
        return default
    out = default.copy()
    out.update(raw)
    deadline = str(out.get("return_deadline", MIDSHIFT_RETURN_DEADLINE_DEFAULT) or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", deadline):
        deadline = MIDSHIFT_RETURN_DEADLINE_DEFAULT
    try:
        threshold = int(float(out.get("late_threshold_minutes", MIDSHIFT_LATE_THRESHOLD_DEFAULT)))
    except Exception:
        threshold = MIDSHIFT_LATE_THRESHOLD_DEFAULT
    out["return_deadline"] = deadline
    out["late_threshold_minutes"] = max(1, min(120, threshold))
    return out
'''.strip("\n"),

    "_phase10_legacy_save_midshift_deadline_config": r'''
def save_midshift_deadline_config(return_deadline, late_threshold_minutes, updated_by):
    deadline = str(return_deadline or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", deadline):
        return False, "Giờ giới hạn phải theo định dạng HH:MM, ví dụ 20:00."
    try:
        threshold = max(1, min(120, int(float(late_threshold_minutes))))
    except Exception:
        return False, "Ngưỡng vào muộn không hợp lệ."

    now = datetime.now(VN_TZ)
    new_value = {
        "return_deadline": deadline,
        "late_threshold_minutes": threshold,
        "updated_date": now.strftime("%d/%m/%Y"),
        "updated_time": now.strftime("%H:%M:%S"),
        "updated_by": str(updated_by or ""),
    }

    def _p10_mirror_midshift():
        result = _phase10_legacy_save_midshift_deadline_config(
            deadline, threshold, updated_by
        )
        try:
            _phase10_legacy_load_midshift_deadline_config.clear()
        except Exception:
            pass
        return result

    def _p10_confirm_midshift():
        try:
            _phase10_legacy_load_midshift_deadline_config.clear()
        except Exception:
            pass
        return _phase10_legacy_load_midshift_deadline_config()

    result = _phase10_commit_control(
        "midshift_deadline_config",
        new_value,
        _p10_mirror_midshift,
        updated_by=str(updated_by or ""),
        operation="midshift_deadline_update",
        confirm_fn=_p10_confirm_midshift,
    )
    try:
        load_midshift_deadline_config.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),
}


def _rename_top_level_function(source: str, old: str, new: str):
    pattern = re.compile(rf"(?m)^def {re.escape(old)}\(")
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        return source, len(matches)
    return pattern.sub(f"def {new}(", source, count=1), 1


def apply(source: str):
    warnings = []
    if MARKER in source:
        return source, warnings

    renamed = source
    for old, new in TARGETS.items():
        renamed, count = _rename_top_level_function(renamed, old, new)
        if count != 1:
            warnings.append(f"phase10_rename_{old}:{count}")

    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        warnings.append(f"phase10_ast:{type(exc).__name__}")
        return source, warnings

    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    missing = [legacy for legacy in TARGETS.values() if legacy not in nodes]
    if missing:
        warnings.extend(f"phase10_node_{name}:0" for name in missing)
        return source, warnings

    lines = renamed.splitlines(keepends=True)
    insertions = []

    first_node = min((nodes[name] for name in TARGETS.values()), key=lambda n: n.lineno)
    first_start = int(first_node.lineno)
    if getattr(first_node, "decorator_list", None):
        first_start = min(first_start, *(int(d.lineno) for d in first_node.decorator_list))
    helper_line = max(0, first_start - 1)
    insertions.append((helper_line, HELPER_BLOCK + "\n\n"))

    for legacy_name, wrapper in WRAPPERS.items():
        node = nodes[legacy_name]
        end_line = int(getattr(node, "end_lineno", node.lineno))
        insertions.append((end_line, "\n\n" + wrapper + "\n"))

    for line_index, text_block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(line_index, text_block)

    patched = "".join(lines)
    try:
        ast.parse(patched)
    except Exception as exc:
        warnings.append(f"phase10_patched_ast:{type(exc).__name__}")
        return source, warnings

    return patched, warnings
