"""Précalcule le temps de trajet entre CHAQUE hôtel géolocalisé et CHAQUE
point d'intérêt, et les stocke dans data/travel_time_cache.json — le même
fichier de cache que l'app utilise en direct. Une fois ce script exécuté,
filtrer par temps de trajet dans l'app (en choisissant un point d'intérêt
dans la liste) devient instantané : plus aucun appel réseau n'est fait au
moment du filtrage, seulement une lecture du cache.

Peut être relancé sans risque : les paires déjà calculées avec succès ne
sont jamais recalculées (voir src/routing.py), donc une interruption
(Ctrl+C, coupure réseau, quota épuisé) n'oblige pas à repartir de zéro.

Usage :
    python scripts/precompute_travel_times.py
    python scripts/precompute_travel_times.py --hotels chemin/vers/hotels.xlsx --poi chemin/vers/poi.xlsx

Par défaut, lit data/hotels.xlsx et data/poi.xlsx (les mêmes fichiers que
l'app charge automatiquement). Pour un calcul fiable dans un temps
raisonnable, configure une clé OpenRouteService dans
.streamlit/secrets.toml (voir .streamlit/secrets.toml.example et le
README) — sans clé, le script utilise le service public OSRM, plus lent
et sans garantie de disponibilité.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import load_hotels, load_pois  # noqa: E402
from src.routing import get_travel_times_minutes, using_ors  # noqa: E402


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

    provider = "OpenRouteService (clé configurée)" if using_ors() else "OSRM public (aucune clé configurée — plus lent, moins fiable)"
    print(f"Service de routage : {provider}")

    hotels = load_hotels(hotels_path)
    pois = load_pois(poi_path)

    geolocated = hotels[hotels["Géolocalisé"]]
    n_total = len(hotels)
    print(f"Hôtels : {n_total} au total, {len(geolocated)} géolocalisés et pris en compte.")
    print(f"Points d'intérêt : {len(pois)}.")
    print(f"Paires à couvrir (hors cache déjà présent) : jusqu'à {len(geolocated) * len(pois):,}\n".replace(",", " "))

    if geolocated.empty or pois.empty:
        sys.exit("Rien à calculer (aucun hôtel géolocalisé ou aucun point d'intérêt).")

    QUOTA_STOP_THRESHOLD = 2  # arrête le script après N échecs consécutifs de type quota/débit
    consecutive_quota_errors = 0
    stopped_early = False

    t0 = time.time()
    errors = []
    i = 0
    for i, (_, poi) in enumerate(pois.iterrows(), start=1):
        label = f"{poi['Nom']} ({poi['Type']})"
        try:
            lat, lon = float(poi["Latitude"]), float(poi["Longitude"])
        except (TypeError, ValueError):
            print(f"[{i}/{len(pois)}] {label} -> ignoré (coordonnées invalides)")
            continue

        t_poi = time.time()
        result, error = get_travel_times_minutes((lat, lon), geolocated)
        n_ok = sum(1 for v in result.values() if v is not None)
        status = "OK" if error is None else f"ERREUR : {error}"
        print(f"[{i}/{len(pois)}] {label} -> {n_ok}/{len(geolocated)} hôtels en {time.time() - t_poi:.1f}s — {status}")
        if error:
            errors.append((label, error))
            consecutive_quota_errors = consecutive_quota_errors + 1 if "quota" in error.lower() else 0
        else:
            consecutive_quota_errors = 0

        if consecutive_quota_errors >= QUOTA_STOP_THRESHOLD:
            stopped_early = True
            print(
                f"\n⏸️  Quota du service de routage atteint (confirmé sur {consecutive_quota_errors} "
                f"points d'intérêt consécutifs) — arrêt du script à {i}/{len(pois)} pour ne pas gaspiller "
                "de temps sur des appels voués à échouer."
            )
            break

    elapsed = time.time() - t0
    print(f"\nArrêté en {elapsed / 60:.1f} min." if stopped_early else f"\nTerminé en {elapsed / 60:.1f} min.")
    if stopped_early:
        print(
            "👉 Rien n'est perdu : relance exactement la même commande plus tard (le quota se renouvelle "
            "généralement chaque jour) — les points d'intérêt déjà réussis ne seront pas recalculés, le "
            "script reprendra automatiquement là où il s'est arrêté."
        )
    elif errors:
        print(f"\n⚠️ {len(errors)} point(s) d'intérêt ont rencontré une erreur au moins une fois")
        print("   (relance le script : le cache garde ce qui a déjà réussi, seul le manquant est retenté) :")
        for label, error in errors:
            print(f"   - {label} : {error}")
    else:
        print("✅ Tous les points d'intérêt ont été calculés sans erreur.")


if __name__ == "__main__":
    main()
