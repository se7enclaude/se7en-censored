"""se7en Censored : floute automatiquement les parties intimes visibles dans une vidéo.

1. ``detect``   : le détecteur NudeNet (ONNX, GPU DirectML) passe sur les images de la vidéo ;
2. ``track``    : les détections deviennent des zones suivies dans le temps (trous comblés, lissage, marges) ;
3. ``effects``  : flou, pixelisation ou bande noire sur une zone (ellipse ou rectangle) ;
4. ``render``   : la vidéo entière est réencodée avec les zones floutées, le son est copié tel quel ;
5. ``pipeline`` : enchaîne tout dans des tâches (analyse, export), avec la progression.
"""
