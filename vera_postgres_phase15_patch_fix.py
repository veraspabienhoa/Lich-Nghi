"""Phase 15 loader hotfix: keep normal shift reads PostgreSQL-primary.

The base Phase 15 patch originally evaluated the legacy shift loader merely to build
its default value before consulting PostgreSQL. That caused an unnecessary Sheets
read even when the PostgreSQL snapshot already existed. This wrapper replaces only
that generated function; all other Phase 15 hooks stay unchanged.
"""
from __future__ import annotations

import vera_postgres_phase15_patch as _base


_FIXED_LOAD_SHIFT_DEFINITIONS = r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_shift_definitions():
    try:
        records, _ = _phase15_read_config(
            _PHASE15_SHIFT_DEFINITIONS_KEY,
            _phase15_shift_source,
            [],
        )
        return _phase15_shift_records_to_df(records)
    except Exception:
        return _phase15_legacy_load_shift_definitions()
'''.strip("\n")


def apply(source):
    key = "_phase15_legacy_load_shift_definitions"
    previous = _base.WRAPPERS.get(key)
    _base.WRAPPERS[key] = _FIXED_LOAD_SHIFT_DEFINITIONS
    try:
        return _base.apply(source)
    finally:
        if previous is None:
            _base.WRAPPERS.pop(key, None)
        else:
            _base.WRAPPERS[key] = previous
