"""Privacy-aware display labels for staff actors in read projections."""

from __future__ import annotations


def staff_actor_display_name(
    full_name: str | None,
    email: str | None,
    *,
    user_exists: bool,
    include_email: bool,
) -> str:
    """Format a staff label without leaking email to name-only read surfaces."""
    if not user_exists:
        return "Unknown user"
    name = full_name.strip() if full_name is not None else ""
    if name:
        if include_email and email:
            return f"{name} ({email})"
        return name
    if include_email:
        return email or "Unknown user"
    return "Unnamed user"
