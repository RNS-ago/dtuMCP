"""Tests for dtumcp.client."""

from _package import mod


def test_number():
    # a course number as text keeps its leading zero
    assert mod._number("02231") == 2231 and mod._number(326352) == 326352 and mod._number("326352") == 326352
    try:
        mod._number("crypto")
        raise AssertionError("not a number")
    except RuntimeError as e:
        assert "02231" in str(e), e
