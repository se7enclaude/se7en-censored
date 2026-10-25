# se7en Censored

**[Télécharger se7en Censored](https://github.com/se7enclaude/se7en-censored/releases/latest/download/se7en-Censored-Setup.exe)**
(ou depuis Seventh OS)

Floute automatiquement les parties intimes visibles dans une vidéo (films, extraits…), uniquement là où il faut,
pour la diffuser sans souci avec les règles de Twitch. Tout tourne sur le PC.

1. On importe une vidéo (fichier, chemin collé ou lien).
2. Le détecteur NudeNet passe sur chaque image (GPU via DirectML) ; les détections deviennent des zones suivies
   dans le temps (trous comblés, lissage, marge).
3. On vérifie : lecteur avec les zones, frise, aperçu flouté, fausses détections retirées, zones ajoutées à la main.
4. Export : flou, pixels ou bande noire ; encodage NVENC, son copié tel quel.

## Code

Base : la même que se7en Clip / se7en Download (`backend/medialab` en Python + FastAPI, interface React dans
`frontend`, fenêtre WebView2, installateur Inno Setup, mises à jour par `latest.json`).

- `backend/medialab/censor/detect.py` : le détecteur (ONNX) et l'analyse reprise après une interruption.
- `backend/medialab/censor/track.py` : des détections aux zones (suivi, hystérésis, comblement, marge, choix de l'utilisateur).
- `backend/medialab/censor/effects.py` et `render.py` : flou / pixels / noir, export par tubes FFmpeg.
- `frontend/src/pages/CensorPage.tsx` : import, progression, vérification, réglages, export.

```
backend\.venv\Scripts\python -m pytest backend\tests\test_censor.py
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Repo se7enclaude/se7en-censored
powershell -ExecutionPolicy Bypass -File packaging\publish.ps1
```

## Licence

AGPL-3.0, comme le détecteur [NudeNet](https://github.com/notAI-tech/NudeNet) (notAI-tech) dont le modèle `640m` est
redistribué ici (release `models`).
