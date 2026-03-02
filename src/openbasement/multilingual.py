"""Language preference resolution for multilingual RDF literals."""

from rdflib import Literal


def resolve_language(
    literals: list[Literal],
    preferred: list[str],
    fallback: str = "any",
) -> dict[str, str]:
    """Collect all language-tagged literals into a language-keyed dict.

    Args:
        literals: List of rdflib Literals (possibly with language tags).
        preferred: Ordered list of preferred language codes (not used for
            filtering, only for ordering untagged literals).
        fallback: "any" to include untagged literals under "_" key,
            "none" to skip untagged literals.

    Returns:
        Dict mapping language code -> value string.
        Untagged literals (if any and fallback="any") appear under "_".
        Empty dict if no literals.
    """
    if not literals:
        return {}

    lang_map: dict[str, str] = {}
    untagged: list[str] = []

    for lit in literals:
        lang = lit.language
        val = str(lit)
        if lang:
            lang_lower = lang.lower()
            if lang_lower not in lang_map:
                lang_map[lang_lower] = val
        else:
            untagged.append(val)

    # Include untagged literals if fallback allows
    if fallback == "any" and untagged and not lang_map:
        lang_map["_"] = untagged[0]

    return lang_map


def pick_best_literal(
    literals: list[Literal],
    preferred: list[str],
    fallback: str = "any",
) -> str | None:
    """Convenience function returning just the best value string.

    Picks the first preferred language available, or any available language.
    """
    lang_map = resolve_language(literals, preferred, fallback)
    if not lang_map:
        return None
    for lang in preferred:
        if lang.lower() in lang_map:
            return lang_map[lang.lower()]
    return next(iter(lang_map.values()))
