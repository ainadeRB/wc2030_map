# WC2030 Maroc — Carte interactive des hôtels

Outil Streamlit type Kepler.gl pour visualiser et filtrer les hôtels et
points d'intérêt (stades, sites d'entraînement, aéroports...) du projet
Coupe du Monde 2030 au Maroc.

## Fonctionnalités

- **Carte interactive**, fond de carte au choix (standard OpenStreetMap,
  clair épuré, satellite, relief — tous sans clé API), avec des bulles :
  - **taille** selon un champ numérique au choix (capacité, chambres
    allouées, PMC, note Booking), avec un curseur d'échelle pour
    agrandir/rétrécir sans perdre la proportionnalité ; un hôtel sans
    valeur pour ce champ garde un point de taille fixe ;
  - **couleur** selon un critère au choix (classement étoiles ou brut,
    catégorie, statut, ville hôte, signature, risque, visite), avec une
    couleur personnalisable par valeur.
- **Couches activables/désactivables** : hôtels, et un type de point
  d'intérêt par couche (une couche = un onglet du fichier POI).
- **Filtres sur (quasiment) toutes les colonnes** du fichier hôtels : ville
  hôte, ville, catégorie, classement, statut, propriétaire, opérateur,
  signatures, risque, visite, capacité, prix moyen (PMC), chambres
  allouées, note Booking, dates (ouverture / dernière et prochaine
  rénovation), recherche par nom.
- **Infos au survol configurables** : choisis les champs affichés dans
  l'infobulle ; le clic affiche toujours la fiche complète. Un champ sans
  valeur s'affiche en italique ("Non renseigné") plutôt que d'être masqué.
- **Photos** : une vignette par hôtel au survol, une version plus grande au
  clic — voir [Photos des hôtels](#photos-des-hôtels) ci-dessous.
- **Filtre par distance** : clique sur la carte (ou saisis une
  latitude/longitude) pour poser un point de référence.
  - **Vol d'oiseau** : rayon en km (toujours utilisé comme pré-filtre).
  - **Temps de trajet (voiture)** : calcule le vrai temps de trajet routier
    (service de routage en ligne), **mis en cache sur disque** — un trajet
    déjà calculé n'est jamais recalculé, même après redémarrage.
  - **Mode Escorte** : réduit le temps de trajet calculé d'un pourcentage
    réglable, pour simuler un déplacement accéléré.
- **KPIs** (nombre d'hôtels, taux de géolocalisation, capacité totale,
  chambres allouées) et légende couleur dynamique.
- **Export** de la sélection filtrée en Excel ou CSV.

## Démarrage rapide

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py   # génère des données de démo (déjà fait une première fois dans data/)
streamlit run app.py
```

L'app s'ouvre avec des données de démonstration (`data/sample_hotels.xlsx`
et `data/sample_poi.xlsx`). Dépose tes propres fichiers Excel via les
uploaders dans la barre latérale pour les remplacer — aucune modification de
code n'est nécessaire.

Chaque upload est **enregistré sur disque** (`data/hotels.xlsx` et
`data/poi.xlsx`, écrasés à chaque nouveau dépôt) : au prochain lancement de
l'app, ce sont automatiquement ces fichiers réels qui sont rechargés — pas
besoin de les re-déposer à chaque fois. Un bouton "Revenir aux données de
démo" dans la barre latérale supprime le fichier persistant et repasse sur
les données d'exemple. Ces fichiers réels ne sont **jamais versionnés dans
git** (voir `.gitignore`) : seules les données de démonstration
(`data/sample_*.xlsx`) le sont.

## Format attendu — fichier hôtels

Les colonnes suivantes sont reconnues automatiquement (les colonnes
manquantes sont simplement ignorées) :

`ID, Ville, Ville hôte, Nom, Catégorie, Nouveau classement assimilé,
Nouveau Statut vérifié, PMC vérif, Capacité act (cha.), 1.VSTH, 2.TBCTH,
3. FIFA HQ, 4. FIFA VIP, 5. FIFA Venue, 6. RBC, 7. Com, 8. Hospi, 9. HB,
10. Media, 11. IBC, #Chambres alloues total, Date ouverture,
Date dernière réno, Date prochaine réno, Propriétaire, Opérateur,
Latitude, Longitude, Signature Prop, Signature Op, Signature,
Note Booking, Risque, Visite`

Seuls les hôtels avec `Latitude`/`Longitude` valides (dans les bornes du
Maroc) sont affichés sur la carte ; les autres restent comptabilisés dans
les KPIs ("Hôtels (base)" vs "Géolocalisés").

## Format attendu — fichier points d'intérêt

**Un onglet par type de point d'intérêt** (comme le fichier réel du projet) :
le nom de l'onglet définit la couche affichée (ex. `Stades`,
`Sites d'entraînement`, `Fan Festival`, `Aéroports`, `Autres`). Dans chaque
onglet, colonnes attendues :

- une colonne "nom" (première colonne de l'onglet, ex. `Stade`, `Aéroport`...),
- `Latitude` et `Longitude` (obligatoires — un onglet sans ces deux colonnes est ignoré),
- `Ville` (optionnelle),
- une éventuelle colonne `Type` propre à l'onglet (ex. sous-catégorie d'un
  site d'entraînement) est conservée comme attribut ("Sous-type"), sans
  créer de couche supplémentaire.

Les noms de colonnes proches (`Lat`/`Latitude`, `Lon`/`Lng`/`Longitude`,
`City`/`Ville`, `Nom`/`Name`) sont détectés automatiquement.

## Photos des hôtels

Dépose des images dans un dossier par hôtel, nommé d'après son `ID` :

```
data/photos/
  HTL-0001/
    facade.jpg
    chambre.jpg
  HTL-0002/
    photo1.png
```

Formats acceptés : `.jpg`, `.jpeg`, `.png`, `.webp`. Aucune modification du
fichier Excel n'est nécessaire — l'app détecte automatiquement les photos au
démarrage. La première photo (ordre alphabétique) sert de vignette au
survol ; le clic affiche une version plus grande et indique le nombre de
photos supplémentaires. Ce dossier n'est **jamais versionné dans git**.

## Temps de trajet et cache

Le mode "Temps de trajet (voiture)" calcule le temps réel entre le point de
référence et chaque hôtel pré-filtré par le rayon vol d'oiseau. Le point de
référence peut être posé en cliquant sur la carte, en choisissant
directement un point d'intérêt dans la liste ("Point de référence = un
point d'intérêt"), ou en saisissant des coordonnées.

Chaque résultat est enregistré dans `data/travel_time_cache.json` (non
versionné) : un trajet déjà calculé pour un point donné n'est **jamais
recalculé**, y compris après redémarrage de l'app. Si le service est
injoignable, un message d'avertissement affiche la raison précise (erreur
réseau, délai dépassé, quota dépassé...) et l'app retombe sur le filtre vol
d'oiseau, sans planter.

### Service de routage : OSRM (par défaut) ou OpenRouteService (recommandé)

Par défaut, l'app utilise le service public **OSRM** — gratuit, sans
inscription, mais sans garantie de disponibilité (c'est une instance de
démonstration, pas prévue pour un usage intensif).

Pour plus de fiabilité, configure une clé **OpenRouteService** (gratuite,
inscription par email sans carte bancaire, sur
https://openrouteservice.org/dev/#/signup) :

1. Copie `.streamlit/secrets.toml.example` en `.streamlit/secrets.toml`.
2. Renseigne ta clé : `ORS_API_KEY = "ta-clé-ici"`.
3. Relance l'app.

`.streamlit/secrets.toml` n'est **jamais versionné dans git** (voir
`.gitignore`) — seul le fichier `.example` (sans clé) l'est. Dès que la clé
est détectée, l'app bascule automatiquement sur OpenRouteService (le bandeau
"Service de routage actif" dans la barre latérale confirme lequel est
utilisé) ; sans clé, elle continue de fonctionner avec OSRM.

### Précalculer tous les trajets hôtels × points d'intérêt

Pour ne plus jamais attendre un calcul au moment du filtrage, on peut
précalculer d'un coup le temps de trajet entre **chaque** hôtel géolocalisé
et **chaque** point d'intérêt :

```bash
python scripts/precompute_travel_times.py
```

Par défaut, le script lit `data/hotels.xlsx` et `data/poi.xlsx` (les mêmes
fichiers que l'app charge) et remplit `data/travel_time_cache.json`. Une
fois terminé, choisir un point d'intérêt dans le filtre "Temps de trajet"
devient instantané pour n'importe quel hôtel présent dans le cache — aucun
appel réseau au moment du filtrage.

- **Sans risque à relancer** : les paires déjà calculées avec succès ne
  sont jamais recalculées (voir la section précédente), donc une
  interruption (Ctrl+C, coupure réseau, quota épuisé) n'oblige pas à
  repartir de zéro — relance simplement la commande.
- **Quota quotidien limité** : les clés OpenRouteService gratuites
  ("Basic") ont un quota journalier qui peut être largement inférieur au
  volume total à calculer (ex. 1800 hôtels × 90 POI). Le script détecte
  l'épuisement du quota (2 échecs consécutifs de ce type) et **s'arrête
  proprement** plutôt que de continuer à échouer sur tous les points
  d'intérêt restants — il indique combien ont été traités et où reprendre.
  **Il suffit de relancer la même commande le lendemain** (le quota se
  renouvelle généralement chaque jour) : le calcul reprend automatiquement
  là où il s'était arrêté, sur plusieurs jours si besoin. Sans clé (OSRM
  public), nettement plus lent et moins fiable.
- **Hôtels sans localisation valide, ou en doublon d'ID** : automatiquement
  ignorés (voir `Géolocalisé` dans le format hôtels ci-dessus) ; en cas de
  doublon d'ID, la ligne géolocalisée est conservée en priorité.
- **Le fichier hôtels utilisé pour ce précalcul peut être minimal** (juste
  `ID`, `Latitude`, `Longitude` — pratique pour ne pas exposer la fiche
  complète d'un hôtel) : le cache résultant est indexé par `ID`, donc
  réutilisable tel quel une fois le fichier hôtels complet chargé dans
  l'app, du moment que les `ID` correspondent.

#### Regroupement par grille (`--grid-km`)

Par défaut (`--grid-km 1`), les hôtels à moins d'1 km les uns des autres
sont regroupés : un seul appel d'API est fait par groupe (au centroïde),
au lieu d'un par hôtel — ex. sur le jeu de données réel du projet (1742
hôtels, 86 POI), ça réduit le nombre de requêtes d'environ **60 %**. Sans
ce regroupement, un quota quotidien serré peut ne jamais suffire à couvrir
tous les hôtels.

Chaque hôtel du groupe ne reçoit **pas** une valeur strictement identique :
le script déduit la vitesse moyenne réellement observée sur le trajet
calculé (distance à vol d'oiseau du centroïde ÷ temps obtenu), puis
l'applique à l'écart de distance à vol d'oiseau entre l'hôtel et le
centroïde. Concrètement, deux hôtels voisins mais pas au même endroit
obtiennent des temps proches mais différents, ancrés sur un vrai calcul de
trajet plutôt qu'une pure estimation à vol d'oiseau.

```bash
python scripts/precompute_travel_times.py --grid-km 2   # groupes plus larges, encore moins de requêtes
python scripts/precompute_travel_times.py --grid-km 0   # désactive le regroupement, calcul exact par hôtel
```

Un run déjà effectué sans regroupement (ou avec une autre taille de
grille) n'est pas perdu en changeant de taille : le script réutilise les
temps déjà calculés par hôtel quand ils existent, avant de faire un
nouvel appel.

#### Estimer les trajets manquants quand le quota est épuisé

Si le quota du service de routage est épuisé avant d'avoir couvert tous les
points d'intérêt, on peut estimer les trajets manquants à partir de ceux
déjà réellement calculés, sans aucun appel réseau :

```bash
python scripts/estimate_travel_times.py
```

Le script calibre un modèle simple (temps ≈ ordonnée à l'origine + pente ×
distance à vol d'oiseau) sur toutes les paires hôtel/POI déjà réellement
mesurées dans `data/travel_time_cache.json`, puis l'applique pour estimer
tout ce qui manque. Les estimations vont dans un fichier **séparé**,
`data/travel_time_estimates.json` — jamais dans le cache réel :
`precompute_travel_times.py` l'ignore complètement et continue de chercher
de vraies valeurs pour tout ce qui manque quand le quota se renouvelle.

Côté app, le filtre "Temps de trajet" utilise automatiquement une vraie
valeur si elle existe, sinon l'estimation, sans appel réseau superflu ; un
message indique combien de valeurs affichées sont des estimations. Le mode
Escorte s'applique normalement dessus, comme sur une vraie valeur.

Relance ce script après chaque nouveau lot de vraies données obtenu (via
`precompute_travel_times.py`) pour affiner le modèle et réduire le nombre
de trajets encore estimés.

## Prochaines étapes possibles

- Passage à 5000 hôtels : la carte utilise déjà le rendu canvas (Leaflet)
  qui tient bien la charge ; si besoin, un mode cluster (`MarkerCluster`)
  ou un pré-filtrage côté serveur peut être ajouté.
- Galerie photo complète dans le popup (actuellement une seule photo,
  agrandie, avec un compteur pour les suivantes).
- Génération automatique de rapports PDF/PPT par ville hôte.
- Connexion directe à une base de données partagée (au lieu d'un upload
  Excel manuel) si plusieurs personnes doivent mettre à jour les données.

## Structure du projet

```
app.py                       # application Streamlit principale
src/
  data_loader.py             # lecture et nettoyage des fichiers Excel
  geo.py                     # distances, bornes de carte
  routing.py                 # temps de trajet (OSRM) + cache disque
  photos.py                  # découverte des photos + génération de vignettes
  styling.py                 # palettes de couleurs, échelle de taille des bulles
scripts/
  generate_sample_data.py    # génère des données de démonstration
data/
  sample_hotels.xlsx
  sample_poi.xlsx
  photos/                    # (optionnel, non versionné) photos par hôtel
```
