## se7en Censored 1.3.0

- **Passages par timecodes.** « Ajouter des timecodes » : colle une liste (« 12:34 - 13:10 », « 1:02:03.500 »,
  HH:MM:SS:images, « 1h02m03s »…), ouvre un .txt, .srt ou .csv, ou une capture d'écran — lue sur ton PC, rien
  n'est envoyé sur Internet. Tu vérifies et corriges les passages avant de les ajouter ; les lignes ignorées sont
  listées avec la raison. Un timecode seul devient un passage de ± 2 s (réglable).
  Chaque passage est flouté **en plein cadre** (par défaut : rien ne peut passer) ou seulement sur les **zones de
  l'IA**. Les passages apparaissent sur la frise.
- **Export bien plus rapide.** Seuls les passages floutés sont réencodés, le reste de l'image est copié tel quel,
  identique à l'original au bit près. Un film 4K HDR de 1 h 42 : 5 min au lieu de 1 h 24.
- **Couleurs fidèles.** L'export garde le codec, les 10 bits, les couleurs, la plage et le HDR (HDR10 avec ses
  métadonnées, HLG) de la source. Avant, un film HDR ressortait en SDR aux couleurs ternes.
- Toute l'interface te tutoie ; l'image affichée après un saut est toujours la bonne (elle pouvait être décalée
  d'une image).

Une vidéo déjà exportée garde l'ancien rendu : exporte à nouveau pour en profiter. Tes projets et tes réglages
sont gardés.

## Installation
Installe ou mets à jour depuis **Seventh OS**, ou télécharge le setup ci-dessous.
