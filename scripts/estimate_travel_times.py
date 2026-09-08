"""Calibre un modèle temps de trajet ≈ f(distance à vol d'oiseau) à partir des
trajets déjà RÉELLEMENT calculés (dans data/travel_time_cache.json, rempli
par precompute_travel_times.py), puis l'applique pour estimer tous les autres
trajets hôtel × point d'intérêt qui n'ont pas encore de valeur réelle.

Le modèle est découpé en 7 TRANCHES de distance, chacune avec sa propre
droite (temps ≈ a + b × distance), plutôt qu'une seule droite globale : la
relation distance → temps n'est pas linéaire sur toute la plage (ville,
route, autoroute n'ont pas la même vitesse effective). Les tranches :
0-1 km, 1-3 km, 3-5 km, 5-10 km, 10-20 km, 20-50 km, 50-150 km.

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


def predict_minutes(models, dist: float) -> float:
    band = assign_band(dist)
    m = models[band]
    return max(0.0, m["a"] + m["b"] * dist)


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

    estimates = json.loads(ESTIMATES_PATH.read_text()) if ESTIMATES_PATH.exists() else {}
    n_total_estimated = 0
    n_total_real = 0
    for _, poi in pois.iterrows():
        try:
            lat, lon = float(poi["Latitude"]), float(poi["Longitude"])
        except (TypeError, ValueError):
            continue
        okey = _origin_key(lat, lon)
        real_bucket = cache.get(okey, {})
        est_bucket = estimates.setdefault(okey, {})
        for _, hotel in geolocated.iterrows():
            hid = str(hotel["ID"])
            if real_bucket.get(hid) is not None:
                n_total_real += 1
                continue
            dist = haversine_km(lat, lon, hotel["Latitude"], hotel["Longitude"])
            est_bucket[hid] = round(predict_minutes(models, dist), 1)
            n_total_estimated += 1

    ESTIMATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ESTIMATES_PATH.write_text(json.dumps(estimates))

    print(f"\n✅ {n_total_estimated:,} paire(s) estimée(s) écrite(s) dans {ESTIMATES_PATH} "
          f"({n_total_real:,} paire(s) déjà réelles, non touchées).".replace(",", " "))
    print(
        "L'app utilisera automatiquement ces estimations quand aucune vraie valeur n'existe "
        "pour un hôtel donné (le mode Escorte s'applique dessus normalement). Relance ce "
        "script après chaque nouveau lot de vraies données pour affiner le modèle."
    )


if __name__ == "__main__":
    main()
