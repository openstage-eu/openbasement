"""Named transforms for post-processing extracted RDF values."""

from typing import Any, Callable


BUILTIN_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "year_from_date": lambda v: v[:4] if isinstance(v, str) and len(v) >= 4 else v,
    "uri_local_name": lambda v: (
        v.rsplit("#", 1)[-1] if "#" in v else v.rsplit("/", 1)[-1]
    )
    if isinstance(v, str)
    else v,
}


def apply_transform(
    value: Any,
    transform_name: str,
    custom_transforms: dict[str, Callable] | None = None,
) -> Any:
    """Apply a named transform to a value.

    Looks up the transform name in custom_transforms first, then built-ins.
    Raises ValueError if the name is not found in either.
    """
    if custom_transforms and transform_name in custom_transforms:
        return custom_transforms[transform_name](value)

    if transform_name in BUILTIN_TRANSFORMS:
        return BUILTIN_TRANSFORMS[transform_name](value)

    raise ValueError(
        f"Unknown transform {transform_name!r}. "
        f"Built-in: {sorted(BUILTIN_TRANSFORMS.keys())}"
    )
