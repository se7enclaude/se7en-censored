## se7en Censored 1.2.0

- **2e passe.** Après l'analyse, les moments où l'IA a vu de la nudité (quelques % de la vidéo) sont relus de plus
  près : l'image est agrandie et lue en morceaux, et un 2e modèle (CLIP) vérifie chaque zone avant de la flouter.
  Moins de fausses alertes (torses d'homme, objets), et les petites parties ou celles au loin sont mieux trouvées.
- **Changements de plan.** Une zone ne déborde plus sur le plan suivant : ni celles de l'IA, ni tes zones qui suivent,
  ni tes zones fixes.
- **Projets déjà analysés :** un bouton « Lancer la 2e passe » suffit, sans refaire l'analyse. L'option
  « 2e vérification » peut être coupée, et le curseur « Sensibilité » règle sa sévérité.
- Le modèle de vérification (352 Mo) est téléchargé une seule fois, au premier usage.

Tes projets et tes réglages sont gardés.

## Installation
Installe ou mets à jour depuis **Seventh OS**, ou télécharge le setup ci-dessous.
