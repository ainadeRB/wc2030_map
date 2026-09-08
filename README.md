# WC2030 Maroc — Carte interactive des hôtels

Outil Streamlit type Kepler.gl pour visualiser et filtrer les hôtels et
points d'intérêt (stades, sites d'entraînement, aéroports...) du projet
Coupe du Monde 2030 au Maroc.

## Fonctionnalités

- **Carte interactive** (fond de carte clair, satellite optionnel via le sélecteur de couches) avec des bulles :
  - **taille** proportionnelle à la capacité de l'hôtel (nombre de chambres),
  - **couleur** selon un critère au choix (classement étoiles, statut, ville hôte, signature, risque, visite).
- **Couches activables/désactivables** : hôtels, et un type de point d'intérêt par couche (stades, sites d'entraînement, aéroports, fan zones...).
- **Filtres sur (quasiment) toutes les colonnes** du fichier hôtels : ville hôte, ville, catégorie, classement, statut, propriétaire, opérateur, signatures, risque, visite, capacité, prix moyen (PMC), chambres allouées, note Booking, dates (ouverture / dernière et prochaine rénovation), recherche par nom.
- **Filtre par distance** : clique sur la carte (ou saisis une latitude/longitude) pour poser un point de référence, puis ne garde que les hôtels dans un rayon donné (en km, calcul à vol d'oiseau). Le calcul en **temps de trajet réel** pourra être branché plus tard sur un moteur de routage (OSRM, etc.).
- **KPIs** (nombre d'hôtels, taux de géolocalisation, capacité totale, chambres allouées) et **répartition par ville hôte**.
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

Colonnes minimales : `Nom`, `Type` (ex. Stade, Site d'entraînement,
Aéroport, Fan Zone...), `Ville`, `Latitude`, `Longitude`. Une couche est
créée automatiquement pour chaque valeur distincte de `Type`. Les noms de
colonnes proches (`Lat`/`Latitude`, `Lon`/`Lng`/`Longitude`, `City`/`Ville`)
sont détectés automatiquement.

## Prochaines étapes possibles

- Passage à 5000 hôtels : la carte utilise déjà le rendu canvas (Leaflet)
  qui tient bien la charge ; si besoin, un mode cluster (`MarkerCluster`)
  ou un pré-filtrage côté serveur peut être ajouté.
- Filtre par **temps de trajet** (isochrones) via une API de routage
  (OSRM auto-hébergé, Mapbox, Google Distance Matrix...).
- Génération automatique de rapports PDF/PPT par ville hôte.
- Connexion directe à une base de données partagée (au lieu d'un upload
  Excel manuel) si plusieurs personnes doivent mettre à jour les données.

## Structure du projet

```
app.py                       # application Streamlit principale
src/
  data_loader.py             # lecture et nettoyage des fichiers Excel
  geo.py                     # distances, bornes de carte
  styling.py                 # palettes de couleurs, échelle de taille des bulles
scripts/
  generate_sample_data.py    # génère des données de démonstration
data/
  sample_hotels.xlsx
  sample_poi.xlsx
```
