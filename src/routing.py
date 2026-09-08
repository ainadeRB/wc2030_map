"""Temps de trajet routier entre un point de référence et des hôtels.

Utilise OpenRouteService (clé API dans .streamlit/secrets.toml, sous
ORS_API_KEY) si configurée, sinon retombe automatiquement sur le service
public OSRM (gratuit, mais sans garantie de disponibilité). Dans les deux
cas, les résultats sont mis en cache sur disque pour ne jamais recalculer
deux fois le même trajet."""
import json
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

CACHE_PATH = Path("data") / "travel_time_cache.json"
REQUEST_TIMEOUT = 15
# Certains services publics bloquent le User-Agent par défaut de `requests`
# (identifié comme un bot) : on s'identifie donc comme un vrai navigateur.
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; wc2030-map/1.0; +streamlit-app)"}

OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_CHUNK_SIZE = 90  # nombre de destinations par appel, pour rester sous les limites du serveur public

ORS_URL = "https://api.openrouteservice.org/v2/matrix/{profile}"
ORS_PROFILES = {"driving": "driving-car"}
ORS_CHUNK_SIZE = 400  # destinations par appel (marge sous la limite habituelle de la matrice ORS)


def _get_ors_api_key():
    try:
        return st.secrets.get("ORS_API_KEY")
    except Exception:
        return None


def using_ors() -> bool:
    """True si une clé OpenRouteService est configurée (utilisée par l'app
    pour afficher quel service de routage est actif)."""
    return bool(_get_ors_api_key())


def _origin_key(lat: float, lon: float) -> str:
    return f"{round(lat, 4)},{round(lon, 4)}"


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _fetch_durations_minutes_osrm(origin, destinations, profile="driving"):
    """Interroge le service public OSRM. Retourne (durées en minutes ou
    None par destination, message d'erreur ou None si succès)."""
    coords = [f"{origin[1]},{origin[0]}"] + [f"{lon},{lat}" for lat, lon in destinations]
    coords_str = ";".join(coords)
    dest_idx = ";".join(str(i) for i in range(1, len(destinations) + 1))
    url = f"{OSRM_BASE_URL}/table/v1/{profile}/{coords_str}?sources=0&destinations={dest_idx}&annotations=duration"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=REQUEST_HEADERS)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "Ok":
            return [None] * len(destinations), f"OSRM : {payload.get('code')} — {payload.get('message', '')}"
        durations_s = payload["durations"][0]
    except requests.Timeout:
        return [None] * len(destinations), "Délai d'attente dépassé en contactant OSRM."
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        return [None] * len(destinations), f"Erreur réseau OSRM ({detail})."
    except (KeyError, ValueError, IndexError) as exc:
        return [None] * len(destinations), f"Réponse inattendue d'OSRM ({exc})."
    return [round(d / 60, 1) if d is not None else None for d in durations_s], None


def _fetch_durations_minutes_ors(origin, destinations, api_key, profile="driving"):
    """Interroge OpenRouteService (Matrix API v2). Retourne (durées en
    minutes ou None par destination, message d'erreur ou None si succès)."""
    ors_profile = ORS_PROFILES.get(profile, "driving-car")
    url = ORS_URL.format(profile=ors_profile)
    locations = [[origin[1], origin[0]]] + [[lon, lat] for lat, lon in destinations]
    body = {
        "locations": locations,
        "sources": [0],
        "destinations": list(range(1, len(destinations) + 1)),
        "metrics": ["duration"],
    }
    headers = {**REQUEST_HEADERS, "Authorization": api_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 401:
            return [None] * len(destinations), "Clé OpenRouteService invalide ou manquante (401)."
        if resp.status_code == 403:
            return [None] * len(destinations), "Accès refusé par OpenRouteService (403) — vérifie ta clé et ton quota."
        if resp.status_code == 429:
            return [None] * len(destinations), "Quota OpenRouteService dépassé pour le moment (429)."
        resp.raise_for_status()
        durations_s = resp.json()["durations"][0]
    except requests.Timeout:
        return [None] * len(destinations), "Délai d'attente dépassé en contactant OpenRouteService."
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        return [None] * len(destinations), f"Erreur réseau OpenRouteService ({detail})."
    except (KeyError, ValueError, IndexError) as exc:
        return [None] * len(destinations), f"Réponse inattendue d'OpenRouteService ({exc})."
    return [round(d / 60, 1) if d is not None else None for d in durations_s], None


def _fetch_durations_minutes(origin, destinations, profile="driving"):
    api_key = _get_ors_api_key()
    if api_key:
        return _fetch_durations_minutes_ors(origin, destinations, api_key, profile=profile)
    return _fetch_durations_minutes_osrm(origin, destinations, profile=profile)


def _chunk_size() -> int:
    return ORS_CHUNK_SIZE if using_ors() else OSRM_CHUNK_SIZE


def get_travel_times_minutes(origin, hotels_df: pd.DataFrame, id_col="ID",
                              lat_col="Latitude", lon_col="Longitude", profile="driving"):
    """Retourne (dict {ID hôtel: minutes ou None}, dernier message d'erreur
    ou None si tout s'est bien passé). Les trajets déjà calculés pour ce
    point de référence (arrondi à ~10 m) sont lus depuis le cache disque ;
    seuls les trajets manquants déclenchent un appel réseau, par lots, puis
    sont ajoutés au cache."""
    cache = _load_cache()
    okey = _origin_key(*origin)
    bucket = cache.setdefault(okey, {})

    rows = hotels_df[[id_col, lat_col, lon_col]].dropna(subset=[lat_col, lon_col])
    missing = [(str(r[id_col]), r[lat_col], r[lon_col]) for _, r in rows.iterrows() if str(r[id_col]) not in bucket]

    results = {}
    newly_cached = False
    last_error = None
    chunk_size = _chunk_size()
    for i in range(0, len(missing), chunk_size):
        chunk = missing[i:i + chunk_size]
        durations, error = _fetch_durations_minutes(origin, [(lat, lon) for _, lat, lon in chunk], profile=profile)
        if error:
            last_error = error
        for (hid, _, _), minutes in zip(chunk, durations):
            results[hid] = minutes
            # Seuls les résultats obtenus sont mis en cache : un échec (None,
            # ex. service injoignable) reste "manquant" et sera retenté au
            # prochain appel, plutôt que d'être figé définitivement.
            if minutes is not None:
                bucket[hid] = minutes
                newly_cached = True

    if newly_cached:
        cache[okey] = bucket
        _save_cache(cache)

    result = {str(hid): bucket.get(str(hid), results.get(str(hid))) for hid in hotels_df[id_col]}
    return result, last_error
