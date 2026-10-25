"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.1.0": {
        "fr": ["Suivi visuel : quand l'IA perd une zone une seconde (mouvement, flou de bougé, plan sombre), elle est "
               "suivie à l'image au lieu de disparaître. Relancez l'analyse d'un projet pour en profiter.",
               "Les zones tracées à la main suivent le mouvement toute seules (jusqu'à 10 s, ou jusqu'à ce qu'elles sortent "
               "du cadre). « Recadrer » relance le suivi.",
               "Raccourcis clavier : Espace, ← →, Maj + ← → (1 s), N / P (zone suivante / précédente), A (ajouter une zone), "
               "R / F (recadrer / finir ici), Suppr (pas une zone), B (aperçu flouté)."],
        "en": ["Visual tracking: when the AI loses a zone for a moment (motion, blur, dark shot), it is followed in the "
               "picture instead of disappearing. Analyse a project again to benefit.",
               "Zones drawn by hand follow the motion on their own (up to 10 s, or until they leave the frame).",
               "Keyboard shortcuts: Space, ← →, Shift + ← → (1 s), N / P, A, R / F, Delete, B."],
    },
    "1.0.1": {
        "fr": ["L'écran de chargement et les réglages sont ceux de se7en Censored (ils montraient encore ceux de "
               "se7en Clip et se7en Download)."],
        "en": ["The loading screen and the settings now belong to se7en Censored (they still showed se7en Clip's "
               "and se7en Download's)."],
    },
    "1.0.0": {
        "fr": ["Première version : les parties intimes visibles sont repérées image par image et floutées, "
               "avec une vérification avant l'export."],
        "en": ["First version: visible intimate areas are found frame by frame and blurred, with a review before "
               "the export."],
    },
}


def since(previous: str, current: str) -> dict[str, list[str]]:
    """Everything that changed after ``previous`` up to ``current``, newest first ("older": since before 1.2)."""
    from .updater import is_newer, parse_version
    out: dict[str, list[str]] = {}
    for v in sorted(CHANGES, key=parse_version, reverse=True):
        if is_newer(v, current):
            continue
        if previous != "older" and not is_newer(v, previous):
            continue
        for lang, items in CHANGES[v].items():
            out.setdefault(lang, []).extend(items)
    return out
