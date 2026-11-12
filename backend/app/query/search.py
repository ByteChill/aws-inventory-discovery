"""Text matching helpers shared by the query modules."""


def like_pattern(value: str) -> str:
    """The `ILIKE` pattern of a case-insensitive contains match, with LIKE's own
    metacharacters escaped (PostgreSQL's default backslash escape)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
