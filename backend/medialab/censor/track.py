"""Des détections image par image aux zones à flouter.

Le détecteur rate parfois une image, tremble d'une image à l'autre ou voit une zone une seule fois. Ici :
  - les détections d'une même classe qui se suivent deviennent une piste (association par recouvrement) ;
  - une piste n'est floutée que si elle est confirmée (vue franchement assez longtemps, pas un torse d'homme) ;
  - les images manquantes au milieu d'une piste sont comblées (interpolation) ;
  - la position est lissée, la zone agrandie d'une marge et gardée un peu avant / après ;
  - les zones ajoutées à la main (images clés) et les pistes masquées par l'utilisateur sont appliquées.

Toutes les boîtes sont en coordonnées normalisées (0..1) : x1, y1, x2, y2.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

LABELS = ["FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
          "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED", "BELLY_COVERED",
          "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED",
          "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED"]

# ce que l'appli propose de flouter (id, nom affiché, coché par défaut)
CLASSES = [
    ("FEMALE_BREAST_EXPOSED", "Poitrines", True),
    ("FEMALE_GENITALIA_EXPOSED", "Sexe féminin", True),
    ("MALE_GENITALIA_EXPOSED", "Sexe masculin", True),
    ("BUTTOCKS_EXPOSED", "Fesses", True),
    ("ANUS_EXPOSED", "Anus", True),
    ("MALE_BREAST_EXPOSED", "Torse masculin", False),
]
CLASS_IDS = {name: LABELS.index(name) for name, _, _ in CLASSES}
DEFAULT_CLASSES = [name for name, _, on in CLASSES if on]
RAW_MIN_SCORE = 0.12  # tout ce qui dépasse est gardé à l'analyse ; la sensibilité filtre ensuite, sans réanalyser


def threshold(sensitivity: int) -> float:
    """Sensibilité 0..100 → seuil de confiance (0 : 0,55, très strict ; 100 : 0,15, floute au moindre doute)."""
    s = max(0, min(100, int(sensitivity)))
    return round(0.55 - 0.40 * s / 100, 4)


def verify_threshold(sensitivity: int) -> float:
    """Note minimale de la 2e vérification (verify.py) : 0,28 par défaut (65), 0,38 très strict (0), 0,1 (100)."""
    s = max(0, min(100, int(sensitivity)))
    v = 0.28 + 0.1 * (65 - s) / 65 if s <= 65 else 0.28 - 0.18 * (s - 65) / 35
    return round(v, 4)


VERIFY_SURE = 0.5    # à partir de cette note, la 2e vérification commence à renforcer une détection…
VERIFY_FULL = 0.65   # … et à partir de celle-ci, la confiance du détecteur est doublée
VERIFY_BOOST = 1.0


def fused(s: np.ndarray, ver: np.ndarray) -> np.ndarray:
    """Confiance du détecteur renforcée par la 2e vérification : une zone que le détecteur voit faiblement mais
    que CLIP juge nettement nue (photo fixe, petite zone, éclairage difficile) compte comme une zone sûre."""
    k = np.clip((np.nan_to_num(ver, nan=0.0) - VERIFY_SURE) / (VERIFY_FULL - VERIFY_SURE), 0.0, 1.0)
    return np.minimum(1.0, s * (1.0 + VERIFY_BOOST * k)).astype(np.float32)


@dataclass
class Params:
    classes: list[str] = field(default_factory=lambda: list(DEFAULT_CLASSES))
    sensitivity: int = 65          # → seuil 0,29
    margin: float = 0.2            # la zone est agrandie de 20 % de sa taille
    pad_s: float = 0.25            # flou gardé 0,25 s avant et après chaque apparition
    gap_s: float = 0.6             # image ratée au milieu d'une piste : trou comblé jusqu'à 0,6 s
    keep_ratio: float = 0.45       # une zone déjà suivie continue jusqu'à 45 % du seuil (hystérésis)
    open_ratio: float = 0.7        # à côté d'une nudité confirmée, une zone peut s'ouvrir à 70 % du seuil
    weak_note: float = 0.45        # … si la 2e vérification la juge nue (note moyenne)
    sure_ratio: float = 1.25       # une zone seule à l'écran doit être sûre en moyenne à 125 % du seuil
    bridge_s: float = 1.5          # deux apparitions de la même zone à moins de 1,5 s (même plan) : une seule zone
    smooth: int = 5                # lissage de la position sur 5 images
    match_iou: float = 0.15        # recouvrement minimal pour continuer une piste
    confirm_s: float = 0.25        # une zone n'est floutée que si le détecteur l'a vue franchement 0,25 s au moins
    male_ratio: float = 0.3        # « poitrine » vue aussi comme torse masculin (même faiblement) : c'est un torse
    covered_ratio: float = 0.8     # « poitrine » vue aussi comme poitrine couverte presque aussi sûre : soutien-gorge
    male_share: float = 0.35       # … sur plus du tiers de ses images : la zone est écartée
    scene_s: float = 1.0           # zone moins sûre gardée si une nudité confirmée est à l'écran à 1 s près
    verify: bool = True            # 2e vérification (si la vidéo l'a eue) : zone jugée non nue par CLIP écartée

    @classmethod
    def from_options(cls, o: dict) -> "Params":
        p = cls()
        if isinstance(o.get("classes"), list):
            p.classes = [c for c in o["classes"] if c in CLASS_IDS]
        if o.get("sensitivity") is not None:
            p.sensitivity = int(o["sensitivity"])
        if o.get("margin") is not None:
            p.margin = max(0.0, min(1.0, float(o["margin"])))
        if o.get("verify") is not None:
            p.verify = bool(o["verify"])
        return p


@dataclass
class Track:
    id: str
    cls: str
    f0: int                      # première image floutée (marge comprise)
    f1: int                      # dernière image floutée (incluse)
    boxes: np.ndarray            # (f1 - f0 + 1, 4)
    score: float
    hits: int

    def box_at(self, f: int) -> np.ndarray | None:
        if self.f0 <= f <= self.f1:
            return self.boxes[f - self.f0]
        return None

    def to_dict(self, decimals: int = 4) -> dict:
        return {"id": self.id, "cls": self.cls, "f0": self.f0, "f1": self.f1, "score": round(self.score, 3),
                "hits": self.hits, "boxes": np.round(self.boxes, decimals).tolist()}


def iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


def _near(a: np.ndarray, b: np.ndarray) -> float:
    """Recouvrement, ou à défaut proximité des centres (zone qui bouge vite, petite boîte) : 0..1."""
    v = iou(a, b)
    if v > 0:
        return v
    ca, cb = (a[:2] + a[2:]) / 2, (b[:2] + b[2:]) / 2
    size = max(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1], 1e-6)
    d = float(np.hypot(*(ca - cb))) / size
    return max(0.0, 0.14 - 0.14 * d)  # centres à moins d'une taille de boîte : encore la même zone


def _smooth(boxes: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(boxes) < 3:
        return boxes
    k = min(k, len(boxes) if len(boxes) % 2 else len(boxes) - 1)
    pad = k // 2
    padded = np.concatenate([np.repeat(boxes[:1], pad, 0), boxes, np.repeat(boxes[-1:], pad, 0)])
    kernel = np.ones(k) / k
    return np.stack([np.convolve(padded[:, j], kernel, mode="valid") for j in range(4)], 1)


def _expand(boxes: np.ndarray, margin: float) -> np.ndarray:
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]
    out = boxes.copy()
    out[:, 0] -= w * margin / 2
    out[:, 2] += w * margin / 2
    out[:, 1] -= h * margin / 2
    out[:, 3] += h * margin / 2
    return np.clip(out, 0.0, 1.0)


def _lookalike(f: np.ndarray, c: np.ndarray, s: np.ndarray, b: np.ndarray, tr: np.ndarray,
               male_ratio: float, covered_ratio: float) -> np.ndarray:
    """Poitrines vues au même endroit, à la même image, comme torse masculin (pecs) ou comme poitrine couverte
    (soutien-gorge, maillot) : le détecteur hésite, et c'est presque toujours l'autre lecture la bonne."""
    out = np.zeros(len(f), bool)
    fb = LABELS.index("FEMALE_BREAST_EXPOSED")
    rivals = {LABELS.index("MALE_BREAST_EXPOSED"): male_ratio, LABELS.index("FEMALE_BREAST_COVERED"): covered_ratio}
    seen: dict[int, list[int]] = {}
    for k in np.flatnonzero(np.isin(c, list(rivals)) & (tr == 0)):
        seen.setdefault(int(f[k]), []).append(int(k))
    for k in np.flatnonzero((c == fb) & (tr == 0)):
        out[k] = any(s[m] >= rivals[int(c[m])] * s[k] and iou(b[k], b[m]) > 0.2 for m in seen.get(int(f[k]), ()))
    return out


def _reach_back(raw: list[dict], f, c, s, b, tr, male, ver, shot, taken: np.ndarray, gap: int, match: float) -> None:
    """Une zone s'ouvre quand le détecteur en est sûr, mais elle était souvent déjà là juste avant, vue plus
    faiblement : on remonte le temps depuis sa première image et on lui rend ces détections laissées de côté."""
    if not raw:
        return
    free: dict[int, list[int]] = {}  # classe → détections libres, dans l'ordre des images
    for k in np.flatnonzero(~taken):
        free.setdefault(int(c[k]), []).append(int(k))
    for t in raw:
        cand = free.get(t["cls"])
        if not cand:
            continue
        fr = f[cand]
        while True:
            first, box = t["obs"][0][0], t["obs"][0][1]
            lo, hi = np.searchsorted(fr, first - gap, "left"), np.searchsorted(fr, first, "left")
            best, best_v = None, match
            for j in range(hi - 1, lo - 1, -1):  # la plus proche dans le temps d'abord
                k = cand[j]
                if taken[k] or shot[k] != t["shot"]:
                    continue
                if best is not None and f[k] != f[best]:
                    break
                v = _near(box, b[k])
                if v >= best_v:
                    best, best_v = k, v
            if best is None:
                break
            taken[best] = True
            t["obs"].insert(0, (int(f[best]), b[best], float(s[best]), bool(tr[best]), bool(male[best]), float(ver[best])))


def _bridge(raw: list[dict], gap: int, match: float) -> list[dict]:
    """La même zone perdue un instant (main qui passe, tête qui tourne) puis retrouvée dans le même plan : une
    seule piste, le trou est comblé — sinon le flou s'éteint puis se rallume."""
    out: list[dict] = []
    last: dict[tuple[int, int], list[dict]] = {}
    for t in sorted(raw, key=lambda t: t["obs"][0][0]):
        best, best_v = None, match
        for o in last.get((t["cls"], t["shot"]), []):
            d = t["obs"][0][0] - o["obs"][-1][0]
            if 0 < d <= gap:
                v = _near(o["obs"][-1][1], t["obs"][0][1])
                if v >= best_v:
                    best, best_v = o, v
        if best is None:
            out.append(t)
            last.setdefault((t["cls"], t["shot"]), []).append(t)
        else:
            best["obs"].extend(t["obs"])
    return out


def build(det: dict, fps: float, frames: int, p: Params, step: int = 1) -> list[Track]:
    """``det`` : tableaux ``f`` (image), ``c`` (classe, index de LABELS), ``s`` (confiance), ``b`` (N×4).
    ``step`` : une image analysée sur ``step`` (mode rapide : 2)."""
    fps = fps or 25.0
    thr = threshold(p.sensitivity)
    low = max(RAW_MIN_SCORE, thr * p.keep_ratio)  # pour continuer une zone ; il faut ``thr`` pour en ouvrir une
    start = max(low, thr * p.open_ratio) if p.verify else thr  # zone faible : gardée seulement en scène de nudité
    gap = max(1, int(round(p.gap_s * fps)))
    pad = int(round(p.pad_s * fps))
    wanted = {CLASS_IDS[c] for c in p.classes if c in CLASS_IDS}
    f, c, s, b = (np.asarray(det.get(k, [])) for k in ("f", "c", "s", "b"))
    tr = np.asarray(det["t"]) if "t" in det and len(det["t"]) == len(f) else np.zeros(len(f), np.int8)
    if not len(f):
        return []
    # 2e passe (refine.py) : note de vérification (NaN : pas vérifiée) et changements de plan
    ver = np.asarray(det["v"], np.float32) if "v" in det and len(det["v"]) == len(f) else np.full(len(f), np.nan)
    cuts = np.asarray(det.get("cuts", []), np.int64)
    vmin = verify_threshold(p.sensitivity) if p.verify else -1.0
    # tr = 1 : position suivie à l'image (detect.Watch), pas vue par le détecteur : ne sert qu'à prolonger une zone
    b = b.astype(np.float32).reshape(-1, 4)
    male = _lookalike(f, c, s.astype(np.float32), b, tr, p.male_ratio, p.covered_ratio)
    s = fused(s.astype(np.float32), ver) if p.verify else s.astype(np.float32)
    keep = np.isin(c, list(wanted)) & ((s >= low) | (tr == 1))
    f, c, s, b, tr, male, ver = f[keep], c[keep], s[keep], b[keep], tr[keep], male[keep], ver[keep]
    order = np.lexsort((-s, tr, f))  # par image, les vraies détections d'abord, les plus confiantes en premier
    f, c, s, b, tr, male, ver = f[order], c[order], s[order], b[order], tr[order], male[order], ver[order]
    shot = np.searchsorted(cuts, f, side="right")  # n° du plan de chaque détection

    taken = np.zeros(len(f), bool)  # détections déjà rangées dans une piste
    raw: list[dict] = []  # pistes en construction : {cls, plan, obs: [(f, box, score, suivie, torse, note)]}
    open_by_cls: dict[int, list[dict]] = {}
    i = 0
    n = len(f)
    while i < n:
        j = i
        while j < n and f[j] == f[i]:
            j += 1
        frame = int(f[i])
        for cls in np.unique(c[i:j]):
            idx = [k for k in range(i, j) if c[k] == cls]
            live = [t for t in open_by_cls.get(int(cls), [])
                    if frame - t["obs"][-1][0] <= gap and t["shot"] == shot[i]]  # jamais d'un plan à l'autre
            open_by_cls[int(cls)] = live
            used: set[int] = set()
            for k in idx:  # plus confiantes d'abord
                best, best_v = None, p.match_iou
                for ti, t in enumerate(live):
                    if ti in used or t["obs"][-1][0] == frame:
                        continue
                    v = _near(t["obs"][-1][1], b[k])
                    if v >= best_v:
                        best, best_v = ti, v
                if best is None:
                    if tr[k] or s[k] < start:
                        continue  # trop incertain (ou seulement suivi) pour ouvrir une nouvelle zone
                    t = {"cls": int(cls), "shot": int(shot[i]),
                         "obs": [(frame, b[k], float(s[k]), False, bool(male[k]), float(ver[k]))]}
                    raw.append(t)
                    live.append(t)
                    used.add(len(live) - 1)
                    taken[k] = True
                else:
                    live[best]["obs"].append((frame, b[k], float(s[k]), bool(tr[k]), bool(male[k]), float(ver[k])))
                    used.add(best)
                    taken[k] = True
        i = j
    _reach_back(raw, f, c, s, b, tr, male, ver, shot, taken, gap, p.match_iou)
    raw = _bridge(raw, max(gap, int(round(p.bridge_s * fps))), p.match_iou)

    # Une zone n'est floutée que si elle est confirmée : vue franchement (au-dessus du seuil) assez longtemps, et
    # sûre en moyenne. Une zone vue plus brièvement n'est gardée que si une nudité confirmée est à l'écran au même
    # moment (même scène). Le reste — verre, bout de bras, reflet vus un instant — est écarté.
    need = max(2, int(round(p.confirm_s * fps / max(1, step))))
    need = min(need, max(1, frames // max(1, step)))  # vidéo très courte (quelques images)
    near = int(round(p.scene_s * fps))
    sure: list[dict] = []
    maybe: list[dict] = []
    for t in raw:
        real = [o for o in t["obs"] if not o[3]]
        scores = np.array([o[2] for o in real])
        if sum(o[4] for o in real) >= p.male_share * len(real):
            continue  # torse d'homme ou soutien-gorge pris pour une poitrine nue
        notes = [o[5] for o in real if not np.isnan(o[5])]
        if notes and float(np.mean(notes)) < vmin:
            continue  # la 2e vérification la juge non nue
        if int((scores >= thr).sum()) >= need and float(scores.mean()) >= min(0.9, thr * p.sure_ratio):
            sure.append(t)
        elif float(scores.max()) < thr:  # ouverte sous le seuil : il faut que la 2e vérification la confirme
            if len(real) >= 2 and notes and float(np.mean(notes)) >= p.weak_note:
                maybe.append(t)
        elif len(real) >= 2 or float(scores.max()) >= thr + 0.15:
            maybe.append(t)
    spans = [(t["obs"][0][0] - near, t["obs"][-1][0] + near) for t in sure]
    maybe = [t for t in maybe if any(lo <= t["obs"][-1][0] and t["obs"][0][0] <= hi for lo, hi in spans)]

    tracks: list[Track] = []
    for t in sorted(sure + maybe, key=lambda t: t["obs"][0][0]):
        obs = t["obs"]
        real = [o for o in obs if not o[3]]
        top = float(np.mean([o[2] for o in real]))  # confiance moyenne (détecteur + 2e vérification), montrée dans la liste
        fs = np.array([o[0] for o in obs])
        bs = np.stack([o[1] for o in obs])
        a, z = int(fs[0]), int(fs[-1])
        grid = np.arange(a, z + 1)
        boxes = np.stack([np.interp(grid, fs, bs[:, j]) for j in range(4)], 1)
        boxes = _smooth(boxes, p.smooth)
        first = int(cuts[t["shot"] - 1]) if t["shot"] > 0 else 0  # marges avant / après : dans le même plan
        last = int(cuts[t["shot"]]) - 1 if t["shot"] < len(cuts) else max(frames - 1, z)
        a2, z2 = max(0, first, a - pad), min(max(frames - 1, z), last, z + pad)
        boxes = np.concatenate([np.repeat(boxes[:1], a - a2, 0), boxes, np.repeat(boxes[-1:], z2 - z, 0)])
        boxes = _expand(boxes, p.margin)
        tracks.append(Track(f"{LABELS[t['cls']]}-{a}-{len(tracks)}", LABELS[t["cls"]], a2, z2, boxes, top, len(real)))
    tracks.sort(key=lambda t: (t.f0, t.f1))
    return tracks


# ------------------------------------------------------------------ choix de l'utilisateur
def mark(track: Track) -> dict:
    """Ce qu'on retient d'une piste retirée ou validée : sa classe, son moment et son endroit moyen dans l'image."""
    return {"cls": track.cls, "f0": track.f0, "f1": track.f1,
            "box": [round(float(v), 4) for v in track.boxes.mean(0)]}


def hidden_match(track: Track, hidden: list[dict]) -> bool:
    """Une piste masquée reste masquée si on change la sensibilité : même classe, même moment (≥ 50 %) et même
    endroit de l'image — deux zones de la même classe à l'écran en même temps restent distinctes. Les choix
    enregistrés avant (sans ``box``) valent pour le moment entier, comme avant."""
    where = None
    for h in hidden:
        if h.get("cls") != track.cls:
            continue
        lo, hi = max(track.f0, int(h["f0"])), min(track.f1, int(h["f1"]))
        if hi < lo:
            continue
        overlap = hi - lo + 1
        if overlap < 0.5 * min(track.f1 - track.f0 + 1, int(h["f1"]) - int(h["f0"]) + 1):
            continue
        box = h.get("box")
        if box and len(box) == 4:
            where = track.boxes.mean(0) if where is None else where
            if iou(where, np.asarray(box, np.float32)) < 0.2:
                continue
        return True
    return False


def manual_box(zone: dict, f: int) -> np.ndarray | None:
    """Zone ajoutée à la main : boîte interpolée entre ses images clés (``keys`` : [{f, box}])."""
    keys = sorted(zone.get("keys") or [], key=lambda k: k["f"])
    if not keys or f < keys[0]["f"] or f > keys[-1]["f"]:
        return None
    if len(keys) == 1:
        return np.asarray(keys[0]["box"], np.float32)
    fs = np.array([k["f"] for k in keys], np.float64)
    bs = np.array([k["box"] for k in keys], np.float32)
    return np.array([np.interp(f, fs, bs[:, j]) for j in range(4)], np.float32)


WHOLE = np.array([0.0, 0.0, 1.0, 1.0], np.float32)
BUCKET = 256  # images par tranche de l'index de FrameBoxes


def clean_segments(segments: list[dict] | None) -> list[dict]:
    """Passages donnés par timecodes : {f0, f1 (inclus), mode « full » (toute l'image floutée) ou « ai » (zones de
    l'IA seulement)}."""
    out = []
    for z in segments or []:
        try:
            f0, f1 = int(z["f0"]), int(z["f1"])
        except (KeyError, TypeError, ValueError):
            continue
        if f1 >= f0 >= 0:
            out.append({**z, "f0": f0, "f1": f1, "mode": "ai" if z.get("mode") == "ai" else "full"})
    return sorted(out, key=lambda z: z["f0"])


class FrameBoxes:
    """Les boîtes à flouter à chaque image (lecture séquentielle rapide pour le rendu).

    ``segments`` : passages donnés par timecodes. Un passage « full » floute toute l'image ; hors des passages « ai »,
    les zones de l'IA ne comptent que si ``ai_outside``."""

    def __init__(self, tracks: list[Track], manual: list[dict] | None = None, hidden: list[dict] | None = None,
                 segments: list[dict] | None = None, ai_outside: bool = True):
        self.tracks = [t for t in tracks if not hidden_match(t, hidden or [])]
        self.manual = [z for z in (manual or []) if z.get("keys")]
        self.segments = clean_segments(segments)
        self.full = [(z["f0"], z["f1"]) for z in self.segments if z["mode"] == "full"]
        self.ai = [(z["f0"], z["f1"]) for z in self.segments if z["mode"] == "ai"]
        self.ai_outside = ai_outside
        # index par tranches d'images : à chaque image, seules les pistes de sa tranche sont regardées (un film de
        # 2 h a des milliers de pistes ; les reparcourir toutes à chaque image rendait l'export très lent)
        self._buckets: dict[int, list[Track]] = {}
        for t in self.tracks:  # déjà dans l'ordre de début : l'ordre est gardé dans chaque tranche
            for k in range(t.f0 // BUCKET, t.f1 // BUCKET + 1):
                self._buckets.setdefault(k, []).append(t)
        # zones à la main : images clés triées une fois pour toutes
        self._manual = []
        for z in self.manual:
            keys = sorted(z["keys"], key=lambda k: k["f"])
            self._manual.append((keys[0]["f"], keys[-1]["f"], np.array([k["f"] for k in keys], np.float64),
                                 np.array([k["box"] for k in keys], np.float32)))

    def ai_here(self, f: int) -> bool:
        return self.ai_outside or any(a <= f <= b for a, b in self.ai)

    def at(self, f: int) -> list[np.ndarray]:
        if any(a <= f <= b for a, b in self.full):
            return [WHOLE]
        out = []
        for t in self._buckets.get(f // BUCKET, ()) if self.ai_here(f) else ():
            if t.f0 > f:
                break
            if t.f1 < f:
                continue
            bx = t.box_at(f)
            if bx is not None:
                out.append(bx)
        for a, b, fs, bs in self._manual:
            if a <= f <= b:
                out.append(bs[0].copy() if len(fs) == 1 else
                           np.array([np.interp(f, fs, bs[:, j]) for j in range(4)], np.float32))
        return out

    def last_frame(self) -> int:
        ends = [t.f1 for t in self.tracks] + [max(k["f"] for k in z["keys"]) for z in self.manual]
        ends += [b for _, b in self.full]
        return max(ends, default=-1)


def coverage(tracks: list[Track], manual: list[dict], hidden: list[dict], fps: float, frames: int,
             segments: list[dict] | None = None, ai_outside: bool = True) -> dict:
    """Résumé pour l'interface : nombre de zones, durée floutée (images où quelque chose est flouté), secondes
    concernées (frise)."""
    fb = FrameBoxes(tracks, manual, hidden, segments, ai_outside)
    fps = fps or 25.0
    on = np.zeros(max(1, frames), bool)
    zones = 0
    for t in fb.tracks:
        span = np.arange(t.f0, min(t.f1, frames - 1) + 1)
        keep = span if fb.ai_outside else span[[fb.ai_here(int(f)) for f in span]]
        if len(keep):
            on[keep] = True
            zones += 1
    for z in fb.manual:
        fs = [k["f"] for k in z["keys"]]
        on[max(0, min(fs)): min(frames, max(fs) + 1)] = True
        zones += 1
    for a, b in fb.full:
        on[a: min(frames, b + 1)] = True
        zones += 1
    secs = max(1, int(np.ceil(frames / fps)))
    hit = np.zeros(secs, bool)
    idx = np.flatnonzero(on)
    hit[np.minimum(secs - 1, (idx / fps).astype(int))] = True
    return {"zones": zones, "seconds": round(len(idx) / fps, 1), "marked_seconds": int(hit.sum())}
