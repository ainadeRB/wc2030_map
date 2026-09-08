"""Repère les hôtels géolocalisés qui n'ont jamais reçu de temps de trajet
valide vers aucun point d'intérêt, dans le cache produit par
precompute_travel_times.py. Un hôtel qui échoue vers TOUS les points
d'intérêt (au lieu d'un échec ponctuel) a presque toujours une coordonnée
non routable : tombée dans l'eau, trop isolée du réseau routier, ou
simplement imprécise/fausse. Utile pour les repérer et corriger la source
plutôt que de les laisser silencieusement disparaître des filtres.

Usage :
    python scripts/find_unreachable_hotels.py [--hotels data/hotels.xlsx]

À lancer de préférence une fois precompute_travel_times.py terminé (sinon
un hôtel peut sembler "non atteint" simplement parce que son tour n'est
pas encore passé) — mais comme chaque point d'intérêt interroge la
totalité des hôtels géolocalisés à chaque passage, un hôtel qui échoue dès
les premiers points d'intérêt du calcul est déjà un signal fiable.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import load_hotels  # noqa: E402
from src.routing import CACHE_PATH  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hotels", default="data/hotels.xlsx", help="Fichier Excel des hôtels (défaut : data/hotels.xlsx)")
    args = parser.parse_args()

    hotels_path = Path(args.hotels)
    if not hotels_path.exists():
        sys.exit(f"Fichier hôtels introuvable : {hotels_path}")
    if not CACHE_PATH.exists():
        sys.exit(f"Aucun cache trouvé ({CACHE_PATH}) — lance d'abord precompute_travel_times.py.")

    hotels = load_hotels(hotels_path)
    geolocated = hotels[hotels["Géolocalisé"]].drop_duplicates(subset="ID", keep="first")
    cache = json.loads(CACHE_PATH.read_text())

    n_origins = len(cache)
    ever_reached = set()
    for bucket in cache.values():
        for hid, minutes in bucket.items():
            if minutes is not None:
                ever_reached.add(hid)

    unreachable = geolocated[~geolocated["ID"].astype(str).isin(ever_reached)]
    print(f"Points d'intérêt présents dans le cache : {n_origins}")
    print(f"Hôtels géolocalisés : {len(geolocated)}")
    print(f"Jamais atteints par aucun point d'intérêt en cache : {len(unreachable)}\n")

    if not unreachable.empty:
        cols = [c for c in ["ID", "Nom", "Latitude", "Longitude"] if c in unreachable.columns]
        print(unreachable[cols].to_string(index=False))
        print(
            "\nCes coordonnées sont probablement non routables (en mer, trop isolées, imprécises...).\n"
            "Vérifie-les sur une carte (Google Maps : colle 'latitude,longitude' dans la recherche) et corrige\n"
            "la source si besoin, puis relance precompute_travel_times.py pour les retenter."
        )
    else:
        print("Tous les hôtels géolocalisés ont été atteints par au moins un point d'intérêt.")


if __name__ == "__main__":
    main()
