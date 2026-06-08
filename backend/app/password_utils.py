import re


def validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."

    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."

    if not re.search(r"[0-9]", password):
        return "Password must include at least one number."

    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must include at least one symbol."

    return None
