"""エラーモデルのユニット（L1）。"""

from __future__ import annotations

from bli_core.errors import ErrorCategory, ErrorObject, ExitCode, make_error


def test_exit_codes():
    assert ExitCode.SUCCESS == 0
    assert ExitCode.TIMEOUT_PENDING == 2
    assert ExitCode.CONNECTION == 3
    assert ExitCode.INPUT == 4


def test_make_error_defaults():
    e = make_error("E_PRECONDITION", cause="no_active_object")
    assert e.kind == "E_PRECONDITION"
    assert e.category == ErrorCategory.PRECONDITION
    assert e.retryable is False
    assert e.cause == "no_active_object"


def test_error_object_to_dict_keys():
    e = ErrorObject(category="INTERNAL", kind="X", retryable=True)
    d = e.to_dict()
    assert set(d) == {
        "category",
        "kind",
        "retryable",
        "cause",
        "userVisibleSymptom",
        "codeBug",
        "remediation",
        "tracebackRef",
    }
    assert d["tracebackRef"] is None
