"""Calibre un modèle simple temps de trajet ≈ f(distance à vol d'oiseau) à
partir des trajets déjà RÉELLEMENT calculés (dans data/travel_time_cache.json,
rempli par precompute_travel_times.py), puis l'applique pour estimer tous les
autres trajets hôtel × point d'intérêt qui n'ont pas encore de valeur réelle.

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

# Utilisé uniquement si aucune vraie donnée n'est disponible pour calibrer
# (repli générique, à affiner dès que le premier vrai calcul est disponible).
FALLBACK_INTERCEPT_MIN = 5.0
FALLBACK_SPEED_KMH = 35.0


def build_training_set(hotels: pd.DataFrame, pois: pd.DataFrame, cache: dict):
    """Construit les paires (distance_km, minutes) à partir de tout ce qui
    est déjà réellement calculé dans le cache, tous points d'intérêt
    confondus, et retourne aussi le détail par point d'intérêt (pour le
    rapport affiché à l'utilisateur)."""
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
            if dist <= 0:
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


def fit_model(distances: np.ndarray, minutes: np.ndarray):
    """Régression linéaire simple minutes ≈ a + b*distance_km. Retourne
    (a, b, r2). Bornes de sécurité : a >= 0, b > 0 (une vitesse positive)."""
    b, a = np.polyfit(distances, minutes, 1)
    a, b = max(0.0, a), max(0.3, b)
    predicted = a + b * distances
    ss_res = float(np.sum((minutes - predicted) ** 2))
    ss_tot = float(np.sum((minutes - minutes.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return a, b, r2


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

    distances, minutes, known_pois = build_training_set(geolocated, pois, cache)

    if len(distances) >= 20:
        a, b, r2 = fit_model(distances, minutes)
        speed_kmh = 60 / b
        print(
            f"\nCalibration à partir de {len(distances):,} paires réelles connues, "
            f"sur {len(known_pois)} point(s) d'intérêt déjà calculé(s) :".replace(",", " ")
        )
        for nom, n in known_pois:
            print(f"  - {nom} : {n} paire(s)")
        print(f"\nModèle : temps (min) ≈ {a:.1f} + {b:.2f} × distance (km)  "
              f"[vitesse implicite ≈ {speed_kmh:.0f} km/h, R² = {r2:.2f}]")
    else:
        a, b = FALLBACK_INTERCEPT_MIN, 60 / FALLBACK_SPEED_KMH
        print(
            f"\n⚠️ Pas assez de vraies données pour calibrer un modèle fiable "
            f"({len(distances)} paire(s) trouvée(s), 20 minimum) — utilisation d'un "
            f"repli générique : {FALLBACK_INTERCEPT_MIN:.0f} min + vitesse moyenne de "
            f"{FALLBACK_SPEED_KMH:.0f} km/h. Relance ce script dès que "
            "precompute_travel_times.py aura calculé au moins quelques points d'intérêt "
            "pour affiner l'estimation."
        )

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
            est_bucket[hid] = round(max(0.5, a + b * dist), 1)
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
