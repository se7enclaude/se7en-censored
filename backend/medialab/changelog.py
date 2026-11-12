"""What changed in each version, shown once after an update ("se7en has been updated")."""
from __future__ import annotations

CHANGES: dict[str, dict[str, list[str]]] = {
    "1.5.2": {
        "fr": ["Corrigé : si la carte graphique décroche pendant l'analyse, l'IA se relance toute seule (et passe sur le processeur s'il le faut) au lieu de s'arrêter sur une erreur.", "Corrigé : un cache des détections abîmé est ignoré, l'analyse est simplement refaite.", "La vérification reste fluide pendant la lecture, même avec beaucoup de zones et de passages.", "Les anciens installateurs de mise à jour sont nettoyés, et les outils (FFmpeg…) s'arrêtent avec l'appli."],
        "en": ["Fixed: if the graphics card drops out during the analysis, the AI restarts on its own (and falls back to the processor if needed) instead of stopping with an error.", "Fixed: a damaged detection cache is ignored, the analysis is simply done again.", "Review stays smooth during playback, even with many zones and passages.", "Old update installers are cleaned up, and the tools (FFmpeg…) stop with the app."],
    },
    "1.5.1": {
        "fr": ["Composants : chacun affiche « À jour (version X, vérifié le …) » ou « Mise à jour disponible » (FFmpeg, le détecteur IA, Deno et yt-dlp). Un composant à jour n'est plus jamais retéléchargé, et l'appli te prévient quand une nouvelle version sort (vérification toutes les 12 h).", "Corrigé : une mise à jour de FFmpeg ou de Deno était téléchargée mais pas utilisée (le bouton retéléchargeait en boucle).", "Corrigé : un ancien yt-dlp téléchargé pouvait masquer le yt-dlp plus récent fourni avec l'appli.", "Corrigé : le bandeau « Terminez l'installation » ne propose plus des moteurs dont se7en Censored ne se sert pas."],
        "en": ["Components: each one shows \"Up to date (version X, checked on …)\" or \"Update available\". A component that is up to date is never downloaded again, and the app tells you when a new version is out (checked every 12 h).", "Fixed: an FFmpeg or Deno update was downloaded but not used (the button downloaded it again and again).", "Fixed: an old downloaded yt-dlp could hide the newer one shipped with the app.", "Fixed: the \"Finish the setup\" banner no longer offers engines se7en Censored does not use."],
    },
    "1.5.0": {
        "fr": ["Nouveau lecteur : lecture fluide de tous les films (même 4K HEVC, HDR, DTS) grâce à une copie "
               "d'aperçu préparée pendant l'analyse, ±10 s (J / L), image par image exacte, vitesse, son, plein écran, "
               "timecode à taper pour y aller, et beaucoup de raccourcis (?).",
               "Aperçu flouté instantané, en pause comme en lecture, même en bougeant les réglages.",
               "Page projet en deux colonnes : le lecteur à gauche, l'export et les onglets Zones IA · Passages · "
               "À la main · Réglages à droite, sans rien faire défiler.",
               "Tu tries les propositions : ✓ pour valider une zone ou un passage, ✕ pour retirer (après "
               "confirmation, elle quitte la liste ; « Retirées » pour revenir en arrière). V et X au clavier, "
               "I / O pour flouter un passage en plein cadre en regardant.",
               "Réglages : « Tu as la dernière version » avec la date de vérification, et chaque composant dit s'il "
               "est à jour : le bouton n'apparaît que s'il y a vraiment une mise à jour."],
        "en": ["New player: smooth playback of every film (even 4K HEVC, HDR, DTS) through a preview copy made during "
               "the analysis, ±10 s (J / L), exact frame stepping, speed, sound, full screen, type a timecode to jump, "
               "and many shortcuts (?).",
               "Instant blurred preview, paused or playing, even while moving the settings.",
               "Two-column project page: the player on the left, export and the AI zones · Passages · Manual · "
               "Settings tabs on the right, no scrolling.",
               "Sort the proposals: ✓ to validate a zone or passage, ✕ to remove it (after confirmation it leaves the "
               "list; \"Removed\" to undo). V and X on the keyboard, I / O to blur a full-frame passage while "
               "watching.",
               "Settings: \"You have the latest version\" with the check date, and each component says whether it "
               "is up to date: the button only shows when there really is an update."],
    },
    "1.4.0": {
        "fr": ["Mode sombre par défaut (changeable dans Réglages › Général).",
               "Vidéos filmées au téléphone (en portrait) : l'export garde une image droite et nette.",
               "Un export ne remplace plus jamais un autre fichier du même nom ; un export interrompu ne laisse plus "
               "de fichiers de travail.",
               "Plus sûr : supprimer un projet demande confirmation, les réglages changés juste avant l'export sont "
               "bien pris, et les raccourcis de la vérification ne s'activent plus depuis les autres onglets.",
               "Plus rapide : export des longs films, aperçu des images, export sur la carte graphique pour les "
               "vidéos HEVC."],
        "en": ["Dark mode by default (change it in Settings › General).",
               "Phone videos (portrait): the export keeps an upright, clean picture.",
               "An export never replaces another file with the same name; an interrupted export no longer leaves "
               "work files behind.",
               "Safer: deleting a project asks first, settings changed right before exporting are used, and review "
               "shortcuts no longer fire from other tabs.",
               "Faster: long films export faster, frame previews are quicker, and HEVC videos export on the GPU."],
    },
    "1.3.1": {
        "fr": ["Analyser seulement des passages : en ajoutant une vidéo, choisis « Seulement des passages » et "
               "colle les timecodes (ou ouvre un fichier, une capture d'écran). L'IA ne vérifie que ces passages, "
               "bien plus vite qu'un film entier. Un passage ajouté plus tard est analysé seul, et « Analyser toute "
               "la vidéo » complète sans refaire ce qui est fait."],
        "en": ["Analyse only some passages: when adding a video, choose \"Only passages\" and paste the "
               "timecodes (or open a file or a screenshot). The AI only checks those passages, much faster than a "
               "whole film. A passage added later is analysed on its own, and \"Analyse the whole video\" completes "
               "the rest without redoing anything."],
    },
    "1.3.0": {
        "fr": ["Passages par timecodes : colle une liste (« 12:34 - 13:10 », « 1:02:03.500 », HH:MM:SS:images…), "
               "ouvre un .txt, .srt ou .csv, ou une capture d'écran (lue sur ton PC). Tu vérifies les passages "
               "avant de les ajouter ; chacun est flouté en plein cadre ou seulement sur les zones de l'IA.",
               "Export bien plus rapide : seuls les passages floutés sont réencodés, le reste de l'image est copié "
               "tel quel, identique à l'original.",
               "Couleurs fidèles : l'export garde le codec, les 10 bits, les couleurs et le HDR (HDR10, HLG) de la "
               "source. Avant, un film HDR ressortait en SDR aux couleurs ternes.",
               "Toute l'interface te tutoie, et l'image demandée est toujours la bonne quand tu sautes dans la "
               "vidéo (elle pouvait être décalée d'une image)."],
        "en": ["Passages by timecodes: paste a list (\"12:34 - 13:10\", \"1:02:03.500\", HH:MM:SS:frames…), open a "
               ".txt, .srt or .csv file, or a screenshot (read on your PC). Check the passages before adding them; "
               "each one is blurred full frame or only on the AI zones.",
               "Much faster export: only the blurred passages are re-encoded, the rest of the picture is copied "
               "as is, identical to the original.",
               "Faithful colours: the export keeps the source's codec, 10-bit depth, colours and HDR (HDR10, HLG). "
               "An HDR film used to come out as dull SDR.",
               "The frame shown after a jump is always the right one (it could be one frame off)."],
    },
    "1.2.2": {
        "fr": ["Flou plus fort : l'intensité agit vraiment, même en 4K. À 70 (par défaut) on ne distingue plus rien, "
               "à 100 tout est effacé. Le flou est le même dans l'aperçu et à l'export.",
               "Aperçu du flou : sous « Intensité », un gros plan d'une zone floutée exactement comme à l'export, mis "
               "à jour quand tu changes les réglages. Un clic t'emmène à ce moment."],
        "en": ["Stronger blur: the intensity really works, even in 4K. At 70 (default) nothing can be made out, at "
               "100 everything is wiped. The blur looks the same in the preview and in the export.",
               "Blur preview: under \"Intensity\", a close-up of a zone blurred exactly as in the export, updated "
               "when you change the settings. Click it to go to that moment."],
    },
    "1.2.1": {
        "fr": ["Export : le son ne disparaît plus. Il était bien dans la vidéo exportée, mais rangé loin devant "
               "l'image, et beaucoup de lecteurs (télé, téléphone, ou après un saut) n'en avaient plus. Les pistes "
               "son et sous-titres gardent aussi leurs réglages d'origine (plus de sous-titres imposés)."],
        "en": ["Export: the sound no longer disappears. It was in the exported video but stored far ahead of the "
               "picture, so many players (TV, phone, or after a jump) had none. Audio and subtitle tracks also keep "
               "their original settings (no more forced subtitles)."],
    },
    "1.2.0": {
        "fr": ["2e passe : après l'analyse, les moments où l'IA a vu de la nudité sont relus de plus près. Un 2e "
               "modèle (CLIP) vérifie chaque zone avant de la flouter : moins de fausses alertes (torses d'homme, "
               "objets). Les petites parties ou celles au loin sont mieux trouvées.",
               "Changements de plan : une zone ne déborde plus jamais sur le plan suivant, qu'elle vienne de l'IA ou "
               "de toi.",
               "Projets déjà analysés : « Lancer la 2e passe » suffit, pas besoin de tout refaire. L'option "
               "« 2e vérification » peut être coupée."],
        "en": ["Second pass: after the analysis, the moments where the AI saw nudity are looked at more closely. A "
               "second model (CLIP) checks each zone before blurring it: fewer false alarms (male chests, objects). "
               "Small or distant parts are found more often.",
               "Shot changes: a zone never spills over into the next shot, whether it comes from the AI or from you.",
               "Projects already analysed: \"Run the second pass\" is enough, no full re-analysis. The \"Second "
               "check\" option can be turned off."],
    },
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
