"""Tests for openbasement.transforms."""

import pytest

from openbasement.transforms import apply_transform, BUILTIN_TRANSFORMS


class TestBuiltinTransforms:
    def test_year_from_date(self):
        assert apply_transform("2023-07-05", "year_from_date") == "2023"

    def test_year_from_date_short_string(self):
        assert apply_transform("20", "year_from_date") == "20"

    def test_year_from_date_non_string(self):
        assert apply_transform(42, "year_from_date") == 42

    def test_uri_local_name_fragment(self):
        assert apply_transform(
            "http://example.org/ontology#MyClass", "uri_local_name"
        ) == "MyClass"

    def test_uri_local_name_path(self):
        assert apply_transform(
            "http://example.org/resource/12345", "uri_local_name"
        ) == "12345"

    def test_uri_local_name_non_string(self):
        assert apply_transform(None, "uri_local_name") is None


class TestCustomTransforms:
    def test_custom_takes_precedence(self):
        custom = {"year_from_date": lambda v: "CUSTOM"}
        assert apply_transform("2023-07-05", "year_from_date", custom) == "CUSTOM"

    def test_custom_transform(self):
        custom = {"uppercase": lambda v: v.upper() if isinstance(v, str) else v}
        assert apply_transform("hello", "uppercase", custom) == "HELLO"

    def test_unknown_transform_raises(self):
        with pytest.raises(ValueError, match="Unknown transform"):
            apply_transform("value", "nonexistent")
