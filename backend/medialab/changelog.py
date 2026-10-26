"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.1.3": {
        "fr": ["Zones à la main : au choix, une « Zone qui suit » le mouvement (A) ou une « Zone fixe » (Z), un cadre "
               "qui reste où tu l'as posé. « Finir ici » (F) l'arrête plus tôt ou l'allonge."],
        "en": ["Manual zones: choose a \"Following zone\" that tracks the movement (A) or a \"Fixed zone\" (Z), a box "
               "that stays where you put it. \"End here\" (F) shortens or extends it."],
    },
    "1.1.2": {
        "fr": ["Détection plus stricte : seule la vraie nudité est floutée. Les fausses alertes d'un instant (verre, "
               "bout de bras, reflet) et les torses d'homme pris pour des poitrines sont écartés. Tes projets déjà "
               "analysés en profitent sans refaire l'analyse."],
        "en": ["Stricter detection: only real nudity is blurred. Brief false alarms (a glass, part of an arm, a "
               "reflection) and male chests mistaken for breasts are left alone. Projects already analysed benefit "
               "without a new analysis."],
    },
    "1.1.1": {
        "fr": ["Nouvelle animation de lancement : « la mosaïque », le logo apparaît sous de gros pixels de censure qui s'affinent jusqu'à devenir net."],
        "en": ['New launch animation: the logo appears under big censor pixels that get finer until it is sharp.'],
    },
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
