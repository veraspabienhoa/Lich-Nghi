"""Source hooks for Phase 12 PostgreSQL-primary UI theme."""
from __future__ import annotations

import ast
import re


MARKER = "_PHASE12_UI_THEME_PATCH_V1 = True"
TARGETS = {
    "load_ui_theme_config": "_phase12_legacy_load_ui_theme_config",
    "save_ui_theme_config": "_phase12_legacy_save_ui_theme_config",
}

HELPER_BLOCK = r'''
_PHASE12_UI_THEME_PATCH_V1 = True


def _phase12_theme_source_payload():
    try:
        _phase12_legacy_load_ui_theme_config.clear()
    except Exception:
        pass
    cfg, err = _phase12_legacy_load_ui_theme_config()
    if err:
        raise RuntimeError(str(err))
    return _normalized_theme_config(cfg)


def _phase12_read_theme(default):
    if vpg is not None:
        fn = getattr(vpg, "phase12_read_theme", None)
        if callable(fn):
            return fn(_phase12_theme_source_payload, default=default)
    try:
        return _phase12_theme_source_payload()
    except Exception:
        return default


def _phase12_commit_theme(value, mirror_fn, updated_by="", confirm_fn=None):
    if vpg is not None:
        fn = getattr(vpg, "phase12_commit_theme", None)
        if callable(fn):
            return fn(
                value,
                mirror_fn,
                updated_by=updated_by,
                confirm_fn=confirm_fn,
            )
    return mirror_fn()
'''.strip("\n")


WRAPPERS = {
    "_phase12_legacy_load_ui_theme_config": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_ui_theme_config():
    default = _normalized_theme_config()
    cfg = _phase12_read_theme(default)
    return _normalized_theme_config(cfg), ""
'''.strip("\n"),

    "_phase12_legacy_save_ui_theme_config": r'''
def save_ui_theme_config(config, username):
    cfg = _normalized_theme_config(config)

    def _p12_mirror_theme():
        result = _phase12_legacy_save_ui_theme_config(config, username)
        try:
            _phase12_legacy_load_ui_theme_config.clear()
        except Exception:
            pass
        return result

    result = _phase12_commit_theme(
        cfg,
        _p12_mirror_theme,
        updated_by=str(username or ""),
        confirm_fn=_phase12_theme_source_payload,
    )
    try:
        load_ui_theme_config.clear()
    except Exception:
        pass
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
            warnings.append(f"phase12_rename_{old}:{count}")

    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        warnings.append(f"phase12_ast:{type(exc).__name__}")
        return source, warnings

    nodes = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [n for n in TARGETS.values() if n not in nodes]
    if missing:
        warnings.extend(f"phase12_node_{n}:0" for n in missing)
        return source, warnings

    lines = renamed.splitlines(keepends=True)
    insertions = []
    first_node = min((nodes[n] for n in TARGETS.values()), key=lambda x: x.lineno)
    first_start = int(first_node.lineno)
    if getattr(first_node, "decorator_list", None):
        first_start = min(first_start, *(int(d.lineno) for d in first_node.decorator_list))
    insertions.append((max(0, first_start - 1), HELPER_BLOCK + "\n\n"))

    for legacy, wrapper in WRAPPERS.items():
        node = nodes[legacy]
        insertions.append((int(getattr(node, "end_lineno", node.lineno)), "\n\n" + wrapper + "\n"))

    for idx, block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(idx, block)

    patched = "".join(lines)
    try:
        ast.parse(patched)
    except Exception as exc:
        warnings.append(f"phase12_patched_ast:{type(exc).__name__}")
        return source, warnings

    return patched, warnings
