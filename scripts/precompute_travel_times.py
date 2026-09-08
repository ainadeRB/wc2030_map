"""Précalcule le temps de trajet entre CHAQUE hôtel géolocalisé et CHAQUE
point d'intérêt, et les stocke dans data/travel_time_cache.json — le même
fichier de cache que l'app utilise en direct. Une fois ce script exécuté,
filtrer par temps de trajet dans l'app (en choisissant un point d'intérêt
dans la liste) devient instantané : plus aucun appel réseau n'est fait au
moment du filtrage, seulement une lecture du cache.

Peut être relancé sans risque : les paires déjà calculées avec succès ne
sont jamais recalculées (voir src/routing.py), donc une interruption
(Ctrl+C, coupure réseau, quota épuisé) n'oblige pas à repartir de zéro.

Regroupement par grille (--grid-km) : par défaut, les hôtels proches les
uns des autres (à moins de --grid-km km) sont regroupés — un seul appel
d'API est fait par groupe (au centroïde du groupe), au lieu d'un par
hôtel. Ça réduit fortement le nombre de requêtes nécessaires (utile face
à un quota quotidien serré). Chaque hôtel du groupe reçoit ensuite un
temps ajusté individuellement à partir de ce résultat : on déduit la
vitesse moyenne réellement observée sur ce trajet (distance à vol
d'oiseau du centroïde ÷ temps calculé), puis on l'applique à l'écart de
distance à vol d'oiseau entre l'hôtel et le centroïde — donc pas une
valeur strictement identique pour tous les hôtels du groupe, juste une
petite extrapolation locale autour d'un vrai calcul de trajet. Utilise
--grid-km 0 pour désactiver le regroupement et calculer un temps exact
(sans aucune extrapolation) pour chaque hôtel individuellement.

Usage :
    python scripts/precompute_travel_times.py
    python scripts/precompute_travel_times.py --hotels chemin/vers/hotels.xlsx --poi chemin/vers/poi.xlsx
    python scripts/precompute_travel_times.py --grid-km 2       # groupes plus larges, moins de requêtes
    python scripts/precompute_travel_times.py --grid-km 0       # pas de regroupement, un calcul par hôtel

Par défaut, lit data/hotels.xlsx et data/poi.xlsx (les mêmes fichiers que
l'app charge automatiquement). Pour un calcul fiable dans un temps
raisonnable, configure une clé OpenRouteService dans
.streamlit/secrets.toml (voir .streamlit/secrets.toml.example et le
README) — sans clé, le script utilise le service public OSRM, plus lent
et sans garantie de disponibilité.
"""
import argparse
import math
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import load_hotels, load_pois  # noqa: E402
from src.geo import haversine_km  # noqa: E402
from src.routing import get_travel_times_minutes, using_ors, _load_cache, _save_cache, _origin_key  # noqa: E402

MIN_IMPLIED_SPEED_KMH = 3.0  # en dessous, l'ajustement de vitesse n'est pas fiable (on garde la valeur brute)


def assign_grid_cells(df: pd.DataFrame, grid_km: float, lat_col="Latitude", lon_col="Longitude") -> pd.Series:
    """Attribue à chaque ligne un identifiant de cellule d'une grille
    d'environ grid_km x grid_km, en degrés approximés localement (adapté à
    l'étendue du Maroc, pas besoin d'une projection cartographique précise
    pour cet usage)."""
    mean_lat = df[lat_col].mean()
    lat_step = grid_km / 111.0
    lon_step = grid_km / (111.0 * math.cos(math.radians(abs(mean_lat))))
    cell_lat = (df[lat_col] // lat_step).astype(int)
    cell_lon = (df[lon_col] // lon_step).astype(int)
    return cell_lat.astype(str) + "_" + cell_lon.astype(str)


def build_cell_targets(geolocated: pd.DataFrame, grid_km: float):
    """Regroupe les hôtels par cellule et retourne (dataframe des cellules
    avec un centroïde par cellule, dict cell_id -> liste d'ID hôtels)."""
    grouped = geolocated.assign(_cell=assign_grid_cells(geolocated, grid_km))
    cells = grouped.groupby("_cell").agg(Latitude=("Latitude", "mean"), Longitude=("Longitude", "mean"))
    cells = cells.reset_index().rename(columns={"_cell": "ID"})
    hotel_ids_by_cell = grouped.groupby("_cell")["ID"].apply(list).to_dict()
    return cells, hotel_ids_by_cell


def preseed_cell_cache_from_hotel_cache(origin, targets: pd.DataFrame, hotel_ids_by_cell: dict) -> int:
    """Si des hôtels d'une cellule ont déjà un temps de trajet en cache pour
    ce point de référence (ex. calculé lors d'un précédent run sans
    regroupement), réutilise cette valeur pour la cellule elle-même plutôt
    que de la recalculer. Retourne le nombre de cellules pré-remplies."""
    cache = _load_cache()
    okey = _origin_key(*origin)
    bucket = cache.setdefault(okey, {})
    n_seeded = 0
    for cell_id in targets["ID"].astype(str):
        if cell_id in bucket:
            continue
        for hotel_id in hotel_ids_by_cell.get(cell_id, []):
            existing = bucket.get(str(hotel_id))
            if existing is not None:
                bucket[cell_id] = existing
                n_seeded += 1
                break
    if n_seeded:
        cache[okey] = bucket
        _save_cache(cache)
    return n_seeded


def fan_out_to_cache(origin, cell_result: dict, hotel_ids_by_cell: dict,
                      cell_centroids: dict, hotel_coords: dict) -> int:
    """Recopie le temps de trajet obtenu par cellule sur chaque hôtel réel de
    cette cellule, directement dans le cache disque partagé avec l'app (qui
    interroge toujours par vrai ID hôtel).

    Plutôt qu'une valeur strictement identique pour tous les hôtels d'une
    cellule (grossier : 1 km peut représenter 2-3 min en voiture), on
    ajuste légèrement le temps de chaque hôtel selon l'écart de distance à
    vol d'oiseau par rapport au centroïde de sa cellule, à la vitesse
    moyenne *réellement observée* sur ce trajet précis (déduite du temps de
    trajet calculé par l'API pour le centroïde) — pas une vitesse
    générique. Un hôtel plus loin du point de référence que le centroïde
    voit son temps ajusté à la hausse, un hôtel plus proche à la baisse.

    Retourne le nombre de hôtels mis à jour."""
    cache = _load_cache()
    okey = _origin_key(*origin)
    bucket = cache.setdefault(okey, {})
    n_written = 0
    for cell_id, minutes in cell_result.items():
        if minutes is None:
            continue
        centroid = cell_centroids.get(cell_id)
        d_centroid = haversine_km(origin[0], origin[1], centroid[0], centroid[1]) if centroid else None
        implied_speed = d_centroid / (minutes / 60) if (d_centroid and minutes > 0.05) else None

        for hotel_id in hotel_ids_by_cell.get(cell_id, []):
            adjusted = minutes
            coords = hotel_coords.get(str(hotel_id))
            if implied_speed and implied_speed >= MIN_IMPLIED_SPEED_KMH and coords:
                d_hotel = haversine_km(origin[0], origin[1], coords[0], coords[1])
                delta_minutes = (d_hotel - d_centroid) / implied_speed * 60
                adjusted = round(max(0.1, minutes + delta_minutes), 1)
            bucket[str(hotel_id)] = adjusted
            n_written += 1
    if n_written:
        cache[okey] = bucket
        _save_cache(cache)
    return n_written


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hotels", default="data/hotels.xlsx", help="Fichier Excel des hôtels (défaut : data/hotels.xlsx)")
    parser.add_argument("--poi", default="data/poi.xlsx", help="Fichier Excel des points d'intérêt (défaut : data/poi.xlsx)")
    parser.add_argument("--grid-km", type=float, default=1.0,
                         help="Taille de la grille de regroupement en km (défaut : 1.0). "
                              "0 pour désactiver et calculer un temps exact par hôtel.")
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

    geolocated = hotels[hotels["Géolocalisé"]].drop_duplicates(subset="ID", keep="first")
    n_total = len(hotels)
    print(f"Hôtels : {n_total} au total, {len(geolocated)} géolocalisés et uniques pris en compte.")
    print(f"Points d'intérêt : {len(pois)}.")

    if geolocated.empty or pois.empty:
        sys.exit("Rien à calculer (aucun hôtel géolocalisé ou aucun point d'intérêt).")

    hotel_coords = {str(r["ID"]): (r["Latitude"], r["Longitude"]) for _, r in geolocated.iterrows()}

    use_grid = args.grid_km > 0
    if use_grid:
        targets, hotel_ids_by_cell = build_cell_targets(geolocated, args.grid_km)
        cell_centroids = {str(r["ID"]): (r["Latitude"], r["Longitude"]) for _, r in targets.iterrows()}
        reduction = 100 * (1 - len(targets) / len(geolocated))
        print(
            f"Regroupement activé : grille de {args.grid_km} km -> {len(targets)} groupe(s) "
            f"au lieu de {len(geolocated)} hôtels (-{reduction:.0f}% de calculs). "
            "Chaque hôtel garde une valeur ajustée individuellement (vitesse réelle du trajet "
            "+ écart de distance à vol d'oiseau dans sa cellule), pas une valeur strictement identique."
        )
    else:
        targets = geolocated.rename(columns={})[["ID", "Latitude", "Longitude"]].copy()
        hotel_ids_by_cell = {str(hid): [hid] for hid in geolocated["ID"]}
        cell_centroids = hotel_coords
        print("Regroupement désactivé (--grid-km 0) : un calcul exact par hôtel.")

    print(f"Paires à couvrir (hors cache déjà présent) : jusqu'à {len(targets) * len(pois):,}\n".replace(",", " "))

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
        if use_grid:
            preseed_cell_cache_from_hotel_cache((lat, lon), targets, hotel_ids_by_cell)
        cell_result, error = get_travel_times_minutes((lat, lon), targets)
        if use_grid:
            fan_out_to_cache((lat, lon), cell_result, hotel_ids_by_cell, cell_centroids, hotel_coords)
        n_ok = sum(len(hotel_ids_by_cell.get(cid, [])) for cid, v in cell_result.items() if v is not None)
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
            "généralement chaque jour) — les groupes déjà réussis ne seront pas recalculés, le script "
            "reprendra automatiquement là où il s'est arrêté."
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
