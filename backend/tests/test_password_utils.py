import pytest

from app.password_utils import validate_password

class TestValidatePassword:
    def test_rejects_password_shorter_than_minimum_length(self):
        assert validate_password("Ab1!") == "Password must be at least 8 characters."

    def test_rejects_password_longer_than_maximum_length(self):
        too_long = "A" + "a" * 70 + "1!"
        assert len(too_long) == 73
        assert validate_password(too_long) == "Password must be at most 72 characters."

    def test_rejects_password_without_uppercase(self):
        assert (
            validate_password("validpass1!")
            == "Password must include at least one uppercase letter."
        )

    def test_rejects_password_without_symbol(self):
        assert validate_password("Validpass1") == "Password must include at least one symbol."

    def test_accepts_valid_password(self):
        assert validate_password("ValidPass1!") is None
