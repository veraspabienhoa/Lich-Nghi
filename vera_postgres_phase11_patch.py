"""Source hooks for Phase 11 PostgreSQL-primary authorization settings."""
from __future__ import annotations

import ast
import re


MARKER = "_PHASE11_AUTH_PATCH_V1 = True"

TARGETS = {
    "load_feature_permissions": "_phase11_legacy_load_feature_permissions",
    "_rewrite_feature_permission_scope": "_phase11_legacy_rewrite_feature_permission_scope",
    "load_shared_input_grants_v92690": "_phase11_legacy_load_shared_input_grants_v92690",
    "grant_shared_input_form_v92690": "_phase11_legacy_grant_shared_input_form_v92690",
    "revoke_shared_input_grant_v92690": "_phase11_legacy_revoke_shared_input_grant_v92690",
}


HELPER_BLOCK = r'''
_PHASE11_AUTH_PATCH_V1 = True


def _phase11_read_auth(setting_key, source_loader, default=None):
    if vpg is not None:
        fn = getattr(vpg, "phase11_read_auth_setting", None)
        if callable(fn):
            return fn(setting_key, source_loader, default=default)
    try:
        return source_loader()
    except Exception:
        return default


def _phase11_commit_auth(
    setting_key,
    value,
    mirror_fn,
    updated_by="",
    operation="update",
    confirm_fn=None,
):
    if vpg is not None:
        fn = getattr(vpg, "phase11_commit_auth_setting", None)
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


def _phase11_feature_payload_from_pair(role_cfg, account_cfg):
    roles = []
    accounts = []
    for key, allowed in dict(role_cfg or {}).items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        target, feature = key
        roles.append({
            "target": str(target or "").strip().lower(),
            "feature": str(feature or "").strip(),
            "allowed": bool(allowed),
        })
    for key, allowed in dict(account_cfg or {}).items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        target, feature = key
        accounts.append({
            "target": normalize_login_name(target),
            "feature": str(feature or "").strip(),
            "allowed": bool(allowed),
        })
    roles.sort(key=lambda x: (x["target"], x["feature"]))
    accounts.sort(key=lambda x: (x["target"], x["feature"]))
    return {"roles": roles, "accounts": accounts}


def _phase11_feature_pair_from_payload(payload):
    role_cfg, account_cfg = {}, {}
    if not isinstance(payload, dict):
        return role_cfg, account_cfg
    for item in payload.get("roles", []) or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", "") or "").strip().lower()
        feature = str(item.get("feature", "") or "").strip()
        if target and feature in FEATURE_DEFINITIONS:
            role_cfg[(target, feature)] = bool(item.get("allowed", False))
    for item in payload.get("accounts", []) or []:
        if not isinstance(item, dict):
            continue
        target = normalize_login_name(item.get("target", ""))
        feature = str(item.get("feature", "") or "").strip()
        if target and feature in FEATURE_DEFINITIONS:
            account_cfg[(target, feature)] = bool(item.get("allowed", False))
    return role_cfg, account_cfg


def _phase11_feature_source_payload():
    try:
        _phase11_legacy_load_feature_permissions.clear()
    except Exception:
        pass
    role_cfg, account_cfg = _phase11_legacy_load_feature_permissions()
    return _phase11_feature_payload_from_pair(role_cfg, account_cfg)


def _phase11_shared_payload_from_df(df):
    cols = list(SHARED_INPUT_HEADERS_V92690)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {"rows": []}
    rows = []
    for _, raw in df.iterrows():
        item = {}
        for c in cols:
            value = raw.get(c, "")
            try:
                if pd.isna(value):
                    value = ""
            except Exception:
                pass
            item[c] = value
        try:
            row_num = int(float(raw.get("__row", 0) or 0))
        except Exception:
            row_num = 0
        if row_num >= 2:
            item["__row"] = row_num
        rows.append(item)
    return {"rows": rows}


def _phase11_shared_df_from_payload(payload):
    cols = list(SHARED_INPUT_HEADERS_V92690)
    rows = []
    if isinstance(payload, dict):
        for item in payload.get("rows", []) or []:
            if not isinstance(item, dict):
                continue
            row = {c: item.get(c, "") for c in cols}
            try:
                row["__row"] = int(float(item.get("__row", 0) or 0))
            except Exception:
                row["__row"] = 0
            rows.append(row)
    return pd.DataFrame(rows, columns=cols + ["__row"]) if rows else pd.DataFrame(columns=cols + ["__row"])


def _phase11_shared_source_payload():
    try:
        _phase11_legacy_load_shared_input_grants_v92690.clear()
    except Exception:
        pass
    return _phase11_shared_payload_from_df(
        _phase11_legacy_load_shared_input_grants_v92690()
    )
'''.strip("\n")


WRAPPERS = {
    "_phase11_legacy_load_feature_permissions": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_feature_permissions():
    default = {"roles": [], "accounts": []}
    payload = _phase11_read_auth(
        "feature_permissions",
        _phase11_feature_source_payload,
        default=default,
    )
    return _phase11_feature_pair_from_payload(payload)
'''.strip("\n"),

    "_phase11_legacy_rewrite_feature_permission_scope": r'''
def _rewrite_feature_permission_scope(scope, target, allowed_features, updated_by, inherit=False):
    scope_norm = normalize_login_name(scope)
    target_text = str(target or "").strip()
    if scope_norm not in {"role", "account"}:
        return False, "Phạm vi phân quyền không hợp lệ."
    if not target_text:
        return False, "Đối tượng phân quyền không hợp lệ."

    role_cfg, account_cfg = load_feature_permissions()
    role_cfg = dict(role_cfg or {})
    account_cfg = dict(account_cfg or {})
    target_norm = normalize_login_name(target_text) if scope_norm == "account" else target_text.lower()

    if scope_norm == "role":
        for key in list(role_cfg):
            if isinstance(key, tuple) and len(key) == 2 and str(key[0]).strip().lower() == target_norm:
                role_cfg.pop(key, None)
        if not inherit:
            allowed_set = set(allowed_features or [])
            for feature in FEATURE_DEFINITIONS:
                role_cfg[(target_norm, feature)] = feature in allowed_set
    else:
        for key in list(account_cfg):
            if isinstance(key, tuple) and len(key) == 2 and normalize_login_name(key[0]) == target_norm:
                account_cfg.pop(key, None)
        if not inherit:
            allowed_set = set(allowed_features or [])
            for feature in FEATURE_DEFINITIONS:
                account_cfg[(target_norm, feature)] = feature in allowed_set

    new_payload = _phase11_feature_payload_from_pair(role_cfg, account_cfg)

    def _p11_mirror_feature():
        result = _phase11_legacy_rewrite_feature_permission_scope(
            scope, target, allowed_features, updated_by, inherit=inherit
        )
        try:
            _phase11_legacy_load_feature_permissions.clear()
        except Exception:
            pass
        return result

    result = _phase11_commit_auth(
        "feature_permissions",
        new_payload,
        _p11_mirror_feature,
        updated_by=str(updated_by or ""),
        operation=f"feature_scope:{scope_norm}:{target_norm}",
        confirm_fn=_phase11_feature_source_payload,
    )
    try:
        load_feature_permissions.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase11_legacy_load_shared_input_grants_v92690": r'''
@st.cache_data(ttl=60, show_spinner=False)
def load_shared_input_grants_v92690():
    payload = _phase11_read_auth(
        "shared_input_grants",
        _phase11_shared_source_payload,
        default={"rows": []},
    )
    return _phase11_shared_df_from_payload(payload)
'''.strip("\n"),

    "_phase11_legacy_grant_shared_input_form_v92690": r'''
def grant_shared_input_form_v92690(form_key, username, granted_by, start_date=None, end_date=None, note=""):
    form_key = str(form_key or "").strip()
    username = str(username or "").strip()
    if form_key not in SHAREABLE_INPUT_FORMS_V92690:
        return False, "Bảng nhập liệu không hợp lệ.", ""
    if not username:
        return False, "Vui lòng chọn tài khoản nhận quyền.", ""

    existing = shared_input_grants_for_user_v92690(username, active_only=True)
    if not existing.empty and existing["Mã bảng"].astype(str).str.strip().eq(form_key).any():
        hit = existing[existing["Mã bảng"].astype(str).str.strip().eq(form_key)].iloc[-1]
        return True, "Tài khoản này đã có quyền chia sẻ đang hiệu lực cho bảng đã chọn.", str(hit.get("Mã chia sẻ", ""))

    now = datetime.now(VN_TZ)
    grant_id = _shared_input_grant_id_v92690(form_key, username)
    cfg = SHAREABLE_INPUT_FORMS_V92690[form_key]
    row = [
        grant_id,
        form_key,
        cfg.get("label", form_key),
        username,
        SHARED_INPUT_ACTIVE_V92690,
        start_date.strftime("%d/%m/%Y") if start_date else "",
        end_date.strftime("%d/%m/%Y") if end_date else "",
        str(note or "").strip(),
        now.strftime("%d/%m/%Y"),
        now.strftime("%H:%M:%S"),
        str(granted_by or "").strip(),
        "", "", "",
    ]

    base = load_shared_input_grants_v92690().copy()
    if not isinstance(base, pd.DataFrame):
        base = pd.DataFrame(columns=list(SHARED_INPUT_HEADERS_V92690) + ["__row"])
    for c in list(SHARED_INPUT_HEADERS_V92690) + ["__row"]:
        if c not in base.columns:
            base[c] = ""
    try:
        max_row = max(
            [int(float(x or 0)) for x in base["__row"].tolist() if str(x).strip()] + [1]
        )
    except Exception:
        max_row = len(base) + 1
    item = dict(zip(SHARED_INPUT_HEADERS_V92690, row))
    item["__row"] = max_row + 1
    new_df = pd.concat([base, pd.DataFrame([item])], ignore_index=True)
    new_payload = _phase11_shared_payload_from_df(new_df)

    def _p11_mirror_grant():
        result = _phase11_legacy_grant_shared_input_form_v92690(
            form_key, username, granted_by,
            start_date=start_date, end_date=end_date, note=note,
        )
        try:
            _phase11_legacy_load_shared_input_grants_v92690.clear()
        except Exception:
            pass
        return result

    result = _phase11_commit_auth(
        "shared_input_grants",
        new_payload,
        _p11_mirror_grant,
        updated_by=str(granted_by or ""),
        operation=f"grant:{form_key}:{normalize_login_name(username)}",
        confirm_fn=_phase11_shared_source_payload,
    )
    try:
        load_shared_input_grants_v92690.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase11_legacy_revoke_shared_input_grant_v92690": r'''
def revoke_shared_input_grant_v92690(grant_id, revoked_by):
    grant_id = str(grant_id or "").strip()
    if not grant_id:
        return False, "Chưa chọn quyền cần thu hồi."

    base = load_shared_input_grants_v92690().copy()
    if not isinstance(base, pd.DataFrame) or base.empty:
        return False, "Không tìm thấy mã chia sẻ."
    hit_mask = base["Mã chia sẻ"].astype(str).str.strip().eq(grant_id)
    if not hit_mask.any():
        return False, "Không tìm thấy mã chia sẻ."

    now = datetime.now(VN_TZ)
    idx = base.index[hit_mask][-1]
    base.at[idx, "Trạng thái"] = SHARED_INPUT_REVOKED_V92690
    base.at[idx, "Ngày thu hồi"] = now.strftime("%d/%m/%Y")
    base.at[idx, "Giờ thu hồi"] = now.strftime("%H:%M:%S")
    base.at[idx, "Người thu hồi"] = str(revoked_by or "").strip()
    new_payload = _phase11_shared_payload_from_df(base)

    def _p11_mirror_revoke():
        result = _phase11_legacy_revoke_shared_input_grant_v92690(grant_id, revoked_by)
        try:
            _phase11_legacy_load_shared_input_grants_v92690.clear()
        except Exception:
            pass
        return result

    result = _phase11_commit_auth(
        "shared_input_grants",
        new_payload,
        _p11_mirror_revoke,
        updated_by=str(revoked_by or ""),
        operation=f"revoke:{grant_id}",
        confirm_fn=_phase11_shared_source_payload,
    )
    try:
        load_shared_input_grants_v92690.clear()
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
            warnings.append(f"phase11_rename_{old}:{count}")

    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        warnings.append(f"phase11_ast:{type(exc).__name__}")
        return source, warnings

    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [legacy for legacy in TARGETS.values() if legacy not in nodes]
    if missing:
        warnings.extend(f"phase11_node_{name}:0" for name in missing)
        return source, warnings

    lines = renamed.splitlines(keepends=True)
    insertions = []

    first_node = min((nodes[name] for name in TARGETS.values()), key=lambda n: n.lineno)
    first_start = int(first_node.lineno)
    if getattr(first_node, "decorator_list", None):
        first_start = min(first_start, *(int(d.lineno) for d in first_node.decorator_list))
    insertions.append((max(0, first_start - 1), HELPER_BLOCK + "\n\n"))

    for legacy_name, wrapper in WRAPPERS.items():
        node = nodes[legacy_name]
        end_line = int(getattr(node, "end_lineno", node.lineno))
        insertions.append((end_line, "\n\n" + wrapper + "\n"))

    for line_index, block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(line_index, block)

    patched = "".join(lines)
    try:
        ast.parse(patched)
    except Exception as exc:
        warnings.append(f"phase11_patched_ast:{type(exc).__name__}")
        return source, warnings

    return patched, warnings
