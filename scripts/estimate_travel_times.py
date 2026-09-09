"""Calibre un modèle temps de trajet ≈ f(distance à vol d'oiseau) à partir des
trajets déjà RÉELLEMENT calculés (dans data/travel_time_cache.json, rempli
par precompute_travel_times.py), puis l'applique pour estimer tous les autres
trajets hôtel × point d'intérêt qui n'ont pas encore de valeur réelle.

Deux niveaux de calibration, du plus précis au plus générique :

1. **Par couple de villes** : quand assez de vraies paires existent déjà
   entre une ville de POI et une ville d'hôtel données (ex. Casablanca →
   Casablanca), on calibre une vitesse moyenne propre à CE couple de
   villes et on estime les trajets manquants du même couple avec elle. Ça
   capture les écarts de trafic locaux (une grande ville congestionnée
   n'a pas la même vitesse effective qu'une zone rurale à distance égale)
   qu'un modèle basé uniquement sur la distance ne peut pas voir. La ville
   de chaque point (hôtel ou POI) est déterminée par proximité géographique
   au centroïde de chaque ville hôte (moyenne des coordonnées des hôtels
   qui lui sont rattachés) plutôt que par un champ "Ville" textuel : ce
   champ est absent de certains onglets du fichier POI (ex. les stades) et
   incohérent d'un onglet à l'autre (ex. "Casa" vs "Casablanca") — la
   proximité géographique fonctionne pour tous les points, sans exception.
2. **Par tranche de distance** (repli) : pour un couple de villes sans
   assez de données réelles, on retombe sur un modèle en 7 TRANCHES de
   distance, chacune avec sa propre droite (temps ≈ a + b × distance),
   plutôt qu'une seule droite globale : la relation distance → temps n'est
   pas linéaire sur toute la plage (ville, route, autoroute n'ont pas la
   même vitesse effective). Les tranches : 0-1 km, 1-3 km, 3-5 km, 5-10 km,
   10-20 km, 20-50 km, 50-150 km.

La tranche 0-1 km passe obligatoirement par l'origine (0 m = 0 min pile) :
en dessous d'1 km on est forcément en trajet local, donc pas de temps
incompressible à ajouter (pas de "min garanti" comme sur les tranches plus
longues où stationnement, feux, sortie de ville, etc. pèsent davantage).
Les tranches suivantes sont raccordées entre elles (le temps prédit à la
borne basse d'une tranche est toujours égal au temps prédit à la borne
haute de la tranche précédente), pour que l'ensemble reste une courbe
continue et croissante plutôt que 7 morceaux disjoints qui pourraient se
contredire (ex. un trajet de 10,1 km plus rapide qu'un trajet de 9,9 km).

Les estimations vont dans un fichier SÉPARÉ, data/travel_time_estimates.json
— jamais dans le cache réel. Ce fichier sert uniquement de repli côté
application (voir get_travel_times_with_fallback dans src/routing.py) quand
aucune vraie valeur n'est disponible : le mode Escorte s'applique dessus
exactement comme sur une vraie valeur. precompute_travel_times.py ignore
totalement ce fichier et continue de chercher de vraies valeurs pour tout ce
qui manque — relance-le simplement quand ton quota se renouvelle, les
estimations correspondantes seront alors remplacées par du réel côté
application (le cache réel prime toujours).

Utile en dépannage quand le quota du service de routage est épuisé mais que
quelques points d'intérêt ont déjà été calculés pour de vrai : on capitalise
dessus plutôt que de se retrouver sans aucun temps de trajet du tout.

Usage :
    python scripts/estimate_travel_times.py
    python scripts/estimate_travel_times.py --hotels chemin/vers/hotels.xlsx --poi chemin/vers/poi.xlsx

À relancer après chaque nouveau lot de vraies valeurs obtenu (le modèle
s'affine avec plus de données, et les paires nouvellement réelles ne sont de
toute façon plus estimées).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import load_hotels, load_pois  # noqa: E402
from src.geo import haversine_km  # noqa: E402
from src.routing import CACHE_PATH, ESTIMATES_PATH, _load_cache, _origin_key  # noqa: E402

# Vitesses jugées invraisemblables : on écarte ces paires de la calibration
# (erreur de données probable) plutôt que de laisser le modèle s'y adapter.
MIN_PLAUSIBLE_SPEED_KMH = 2.0
MAX_PLAUSIBLE_SPEED_KMH = 130.0

# 7 tranches de distance (km), chacune calibrée séparément — la relation
# distance → temps n'est pas la même en ville (courte distance) qu'à
# l'approche de trajets d'autoroute (longue distance). Bornes hautes
# exclusives sauf la dernière. La borne haute de la dernière tranche est
# aussi la distance max d'entraînement (au-delà, la tranche est réutilisée
# telle quelle par extrapolation — au-delà de 150 km sort de toute façon du
# rayon de recherche utilisable dans l'app).
DISTANCE_BANDS = [
    (0.0, 1.0),
    (1.0, 3.0),
    (3.0, 5.0),
    (5.0, 10.0),
    (10.0, 20.0),
    (20.0, 50.0),
    (50.0, 150.0),
]

# Nombre minimum de paires réelles requis pour calibrer une tranche donnée ;
# la première tranche (un seul paramètre, droite forcée par l'origine) peut
# se contenter de moins de points que les autres (deux paramètres).
MIN_PAIRS_BAND0 = 5
MIN_PAIRS_OTHER = 8

# Pente minimale acceptée (= vitesse max plausible), pour ne jamais prédire
# une vitesse irréaliste même avec peu de données bruitées dans une tranche.
MIN_SLOPE_MIN_PER_KM = 60.0 / MAX_PLAUSIBLE_SPEED_KMH

# Repli utilisé tranche par tranche quand aucune vraie donnée ne permet de
# calibrer localement : vitesse effective croissante avec la distance
# (ville → route → autoroute), pour un comportement réaliste par défaut en
# attendant que precompute_travel_times.py ait couvert davantage de trajets.
FALLBACK_SPEEDS_KMH = [18.0, 25.0, 30.0, 40.0, 55.0, 80.0, 100.0]

# Nombre minimum de vraies paires requis entre un couple de villes donné
# pour lui faire confiance plutôt que de retomber sur le modèle générique
# par tranche de distance — un couple avec seulement 1 ou 2 paires ne
# donnerait qu'un bruit de mesure, pas une vitesse locale fiable.
MIN_PAIRS_PER_CITY_PAIR = 5


def compute_city_centroids(hotels: pd.DataFrame) -> dict:
    """Centroïde (lat, lon) de chaque ville hôte, à partir des hôtels
    géolocalisés qui lui sont rattachés (colonne "Ville hôte"). Sert de
    référence pour déterminer la ville la plus proche de n'importe quel
    point (hôtel ou POI), y compris ceux sans champ "Ville" exploitable."""
    if "Ville hôte" not in hotels.columns:
        return {}
    valid = hotels.dropna(subset=["Ville hôte", "Latitude", "Longitude"])
    return {
        city: (group["Latitude"].mean(), group["Longitude"].mean())
        for city, group in valid.groupby("Ville hôte")
    }


def nearest_city(lat: float, lon: float, centroids: dict):
    """Ville hôte la plus proche de (lat, lon) à vol d'oiseau, ou None si
    aucun centroïde n'est disponible."""
    if not centroids:
        return None
    return min(centroids, key=lambda city: haversine_km(lat, lon, *centroids[city]))


# Un hôtel rattaché à une ville hôte peut être physiquement loin de son
# centre (grande banlieue, zone rurale) : appliquer la vitesse calibrée
# "trafic urbain" d'un couple de villes à une distance bien plus grande que
# tout ce qui a servi à la calibrer serait une extrapolation hasardeuse
# (ex. un trajet de 47 km dans un couple calibré sur des trajets de 5-15 km
# n'est probablement plus un trajet urbain mais implique de la route/
# autoroute). On limite donc l'usage de la vitesse d'un couple à une marge
# raisonnable au-delà de la distance réelle la plus longue observée pour ce
# couple ; au-delà, repli sur le modèle générique par tranche.
CITY_PAIR_EXTRAPOLATION_MARGIN = 1.3


def build_city_pair_speeds(hotels: pd.DataFrame, pois: pd.DataFrame, cache: dict, centroids: dict):
    """Calcule, pour chaque couple (ville du POI, ville de l'hôtel) avec
    assez de vraies paires mesurées, une vitesse moyenne implicite (km/h)
    propre à ce couple — pondérée par la distance de chaque paire, pour ne
    pas laisser un seul trajet très court dominer la moyenne. Retourne
    {(ville_poi, ville_hotel): (vitesse_kmh, n_paires, distance_max_observée)}."""
    max_dist = DISTANCE_BANDS[-1][1]
    pair_dist_sum: dict = {}
    pair_minutes_sum: dict = {}
    pair_n: dict = {}
    pair_max_dist: dict = {}

    for _, poi in pois.iterrows():
        try:
            lat, lon = float(poi["Latitude"]), float(poi["Longitude"])
        except (TypeError, ValueError):
            continue
        okey = _origin_key(lat, lon)
        bucket = cache.get(okey)
        if not bucket:
            continue
        poi_city = nearest_city(lat, lon, centroids)
        if poi_city is None:
            continue
        for _, hotel in hotels.iterrows():
            hid = str(hotel["ID"])
            minutes = bucket.get(hid)
            if minutes is None:
                continue
            hotel_city = hotel.get("Ville hôte")
            if hotel_city is None or (isinstance(hotel_city, float) and pd.isna(hotel_city)):
                hotel_city = nearest_city(hotel["Latitude"], hotel["Longitude"], centroids)
            if hotel_city is None:
                continue
            dist = haversine_km(lat, lon, hotel["Latitude"], hotel["Longitude"])
            if dist <= 0 or dist > max_dist:
                continue
            speed = dist / (minutes / 60)
            if not (MIN_PLAUSIBLE_SPEED_KMH <= speed <= MAX_PLAUSIBLE_SPEED_KMH):
                continue
            key = (poi_city, hotel_city)
            pair_dist_sum[key] = pair_dist_sum.get(key, 0.0) + dist
            pair_minutes_sum[key] = pair_minutes_sum.get(key, 0.0) + minutes
            pair_n[key] = pair_n.get(key, 0) + 1
            pair_max_dist[key] = max(pair_max_dist.get(key, 0.0), dist)

    city_pair_speeds = {}
    for key, n in pair_n.items():
        if n < MIN_PAIRS_PER_CITY_PAIR:
            continue
        speed_kmh = pair_dist_sum[key] / (pair_minutes_sum[key] / 60.0)
        city_pair_speeds[key] = (speed_kmh, n, pair_max_dist[key])
    return city_pair_speeds


def build_training_pairs(hotels: pd.DataFrame, pois: pd.DataFrame, cache: dict):
    """Construit toutes les paires (distance_km, minutes) à partir de tout ce
    qui est déjà réellement calculé dans le cache, tous points d'intérêt
    confondus, et retourne aussi le détail par point d'intérêt (pour le
    rapport affiché à l'utilisateur). Ne filtre pas encore par tranche."""
    max_dist = DISTANCE_BANDS[-1][1]
    distances, minutes_list = [], []
    known_pois = []
    for _, poi in pois.iterrows():
        try:
            lat, lon = float(poi["Latitude"]), float(poi["Longitude"])
        except (TypeError, ValueError):
            continue
        okey = _origin_key(lat, lon)
        bucket = cache.get(okey)
        if not bucket:
            continue
        n_pairs_here = 0
        for _, hotel in hotels.iterrows():
            hid = str(hotel["ID"])
            minutes = bucket.get(hid)
            if minutes is None:
                continue
            dist = haversine_km(lat, lon, hotel["Latitude"], hotel["Longitude"])
            if dist <= 0 or dist > max_dist:
                continue
            speed = dist / (minutes / 60)
            if not (MIN_PLAUSIBLE_SPEED_KMH <= speed <= MAX_PLAUSIBLE_SPEED_KMH):
                continue
            distances.append(dist)
            minutes_list.append(minutes)
            n_pairs_here += 1
        if n_pairs_here:
            known_pois.append((poi["Nom"], n_pairs_here))
    return np.array(distances), np.array(minutes_list), known_pois


def assign_band(dist: float) -> int:
    """Retourne l'indice de la tranche (dans DISTANCE_BANDS) à laquelle
    appartient cette distance. Au-delà de la dernière borne, on reste sur la
    dernière tranche (extrapolation plutôt que valeur manquante)."""
    for i, (lo, hi) in enumerate(DISTANCE_BANDS):
        if dist < hi or i == len(DISTANCE_BANDS) - 1:
            return i
    return len(DISTANCE_BANDS) - 1


def _r2(distances, minutes, a, b):
    predicted = a + b * distances
    ss_res = float(np.sum((minutes - predicted) ** 2))
    ss_tot = float(np.sum((minutes - minutes.mean()) ** 2))
    return 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def fit_bands(distances: np.ndarray, minutes: np.ndarray):
    """Calibre les 7 modèles (un par tranche de DISTANCE_BANDS), raccordés
    entre eux (le temps prédit à la borne basse d'une tranche == le temps
    prédit à la borne haute de la précédente), pour rester une courbe
    continue et croissante. Retourne une liste de dicts, un par tranche :
    {a, b, n, r2, fallback}."""
    models = []
    prev_boundary_value = 0.0  # temps prédit à distance 0 (tranche 0 passe par l'origine)

    for i, (lo, hi) in enumerate(DISTANCE_BANDS):
        mask = (distances >= lo) & (distances < hi if i < len(DISTANCE_BANDS) - 1 else distances <= hi)
        d_band, m_band = distances[mask], minutes[mask]
        fallback_speed = FALLBACK_SPEEDS_KMH[i]

        if i == 0:
            # Tranche 0-1 km : droite forcée par l'origine (0 m = 0 min).
            min_pairs = MIN_PAIRS_BAND0
            if len(d_band) >= min_pairs:
                b = float(np.sum(d_band * m_band) / np.sum(d_band ** 2))
                b = max(MIN_SLOPE_MIN_PER_KM, b)
                r2 = _r2(d_band, m_band, 0.0, b)
                fallback = False
            else:
                b = 60.0 / fallback_speed
                r2 = float("nan")
                fallback = True
            a = 0.0
        else:
            min_pairs = MIN_PAIRS_OTHER
            if len(d_band) >= min_pairs:
                b, a = np.polyfit(d_band, m_band, 1)
                b = max(MIN_SLOPE_MIN_PER_KM, float(b))
                a = max(0.0, float(a))
                # R² du fit brut, avant raccord : reflète la qualité du fit
                # local sur les données de cette tranche, indépendamment de
                # l'ajustement d'ordonnée à l'origine fait juste après.
                r2 = _r2(d_band, m_band, a, b)
                # Raccord : le temps prédit à la borne basse de cette tranche
                # doit correspondre à celui prédit par la tranche précédente
                # à cette même borne, pour éviter toute cassure ou baisse de
                # temps quand la distance augmente. Seule l'ordonnée à
                # l'origine est corrigée (la pente, issue des données de la
                # tranche, est conservée telle quelle).
                a = prev_boundary_value - b * lo
                fallback = False
            else:
                b = 60.0 / fallback_speed
                a = prev_boundary_value - b * lo
                r2 = float("nan")
                fallback = True

        models.append({"lo": lo, "hi": hi, "a": a, "b": b, "n": len(d_band), "r2": r2, "fallback": fallback})
        prev_boundary_value = a + b * hi

    return models


def predict_minutes(models, dist: float, city_pair_speeds: dict = None, city_key=None) -> float:
    """Estime le temps (min) pour une distance donnée : priorité à la
    vitesse calibrée pour ce couple de villes précis (city_key) si elle
    existe ET que `dist` reste dans une marge raisonnable de ce qui a
    servi à la calibrer (pas d'extrapolation hasardeuse vers une distance
    jamais observée pour ce couple), sinon repli sur le modèle générique
    par tranche de distance."""
    if city_pair_speeds and city_key in city_pair_speeds:
        speed_kmh, _, max_dist_observed = city_pair_speeds[city_key]
        if dist <= max_dist_observed * CITY_PAIR_EXTRAPOLATION_MARGIN:
            return max(0.0, dist / speed_kmh * 60.0)
    band = assign_band(dist)
    m = models[band]
    return max(0.0, m["a"] + m["b"] * dist)


def print_city_pair_report(city_pair_speeds: dict):
    if not city_pair_speeds:
        print("\nAucun couple de villes n'a assez de vraies paires (min. "
              f"{MIN_PAIRS_PER_CITY_PAIR}) pour une calibration dédiée — "
              "modèle générique par tranche utilisé partout pour l'instant.")
        return
    print(f"\nVitesses calibrées par couple de villes ({len(city_pair_speeds)} couple(s), "
          f"min. {MIN_PAIRS_PER_CITY_PAIR} paires réelles) :")
    for (city_poi, city_hotel), (speed_kmh, n, max_dist_observed) in sorted(city_pair_speeds.items(), key=lambda kv: -kv[1][1]):
        arrow = "trajets internes" if city_poi == city_hotel else "→"
        label = f"{city_poi} ({arrow})" if city_poi == city_hotel else f"{city_poi} → {city_hotel}"
        applies_up_to = max_dist_observed * CITY_PAIR_EXTRAPOLATION_MARGIN
        print(f"  - {label:<35} {speed_kmh:.0f} km/h implicite ({n} paire(s) réelle(s), "
              f"appliqué jusqu'à ~{applies_up_to:.0f} km)")


def print_report(models, known_pois):
    print(f"\nCalibration en {len(DISTANCE_BANDS)} tranches, à partir de {sum(m['n'] for m in models):,} paires réelles connues, "
          f"sur {len(known_pois)} point(s) d'intérêt déjà calculé(s) :".replace(",", " "))
    for nom, n in known_pois:
        print(f"  - {nom} : {n} paire(s)")
    print()
    for m in models:
        speed_kmh = 60.0 / m["b"]
        label = f"{m['lo']:g}-{m['hi']:g} km"
        eq = f"temps (min) ≈ {m['a']:.2f} + {m['b']:.2f} × distance (km)"
        if m["fallback"]:
            print(f"  [{label:>10}] {eq}  [repli, seulement {m['n']} paire(s) réelle(s), vitesse générique {speed_kmh:.0f} km/h]")
        else:
            print(f"  [{label:>10}] {eq}  [vitesse implicite ≈ {speed_kmh:.0f} km/h, R² = {m['r2']:.2f}, {m['n']} paire(s)]")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hotels", default="data/hotels.xlsx", help="Fichier Excel des hôtels (défaut : data/hotels.xlsx)")
    parser.add_argument("--poi", default="data/poi.xlsx", help="Fichier Excel des points d'intérêt (défaut : data/poi.xlsx)")
    args = parser.parse_args()

    hotels_path, poi_path = Path(args.hotels), Path(args.poi)
    if not hotels_path.exists():
        sys.exit(f"Fichier hôtels introuvable : {hotels_path}")
    if not poi_path.exists():
        sys.exit(f"Fichier POI introuvable : {poi_path}")
    if not CACHE_PATH.exists():
        sys.exit(f"Aucun cache de trajets réels trouvé ({CACHE_PATH}) — lance d'abord precompute_travel_times.py au moins une fois.")

    hotels = load_hotels(hotels_path)
    pois = load_pois(poi_path)
    geolocated = hotels[hotels["Géolocalisé"]].drop_duplicates(subset="ID", keep="first")
    cache = _load_cache()

    print(f"Hôtels géolocalisés et uniques : {len(geolocated)}")
    print(f"Points d'intérêt : {len(pois)}")

    distances, minutes, known_pois = build_training_pairs(geolocated, pois, cache)
    models = fit_bands(distances, minutes)
    print_report(models, known_pois)

    centroids = compute_city_centroids(geolocated)
    city_pair_speeds = build_city_pair_speeds(geolocated, pois, cache, centroids)
    print_city_pair_report(city_pair_speeds)

    estimates = json.loads(ESTIMATES_PATH.read_text()) if ESTIMATES_PATH.exists() else {}
    n_total_estimated = 0
    n_total_real = 0
    n_via_city_pair = 0
    for _, poi in pois.iterrows():
        try:
            lat, lon = float(poi["Latitude"]), float(poi["Longitude"])
        except (TypeError, ValueError):
            continue
        okey = _origin_key(lat, lon)
        real_bucket = cache.get(okey, {})
        est_bucket = estimates.setdefault(okey, {})
        poi_city = nearest_city(lat, lon, centroids)
        for _, hotel in geolocated.iterrows():
            hid = str(hotel["ID"])
            if real_bucket.get(hid) is not None:
                n_total_real += 1
                continue
            dist = haversine_km(lat, lon, hotel["Latitude"], hotel["Longitude"])
            hotel_city = hotel.get("Ville hôte")
            if hotel_city is None or (isinstance(hotel_city, float) and pd.isna(hotel_city)):
                hotel_city = nearest_city(hotel["Latitude"], hotel["Longitude"], centroids)
            city_key = (poi_city, hotel_city)
            if city_key in city_pair_speeds and dist <= city_pair_speeds[city_key][2] * CITY_PAIR_EXTRAPOLATION_MARGIN:
                n_via_city_pair += 1
            est_bucket[hid] = round(predict_minutes(models, dist, city_pair_speeds, city_key), 1)
            n_total_estimated += 1

    ESTIMATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ESTIMATES_PATH.write_text(json.dumps(estimates))

    print(f"\n✅ {n_total_estimated:,} paire(s) estimée(s) écrite(s) dans {ESTIMATES_PATH} "
          f"({n_total_real:,} paire(s) déjà réelles, non touchées) — dont {n_via_city_pair:,} "
          "via une vitesse calibrée par couple de villes, le reste via le modèle générique "
          "par tranche.".replace(",", " "))
    print(
        "L'app utilisera automatiquement ces estimations quand aucune vraie valeur n'existe "
        "pour un hôtel donné (le mode Escorte s'applique dessus normalement). Relance ce "
        "script après chaque nouveau lot de vraies données pour affiner le modèle."
    )


if __name__ == "__main__":
    main()
