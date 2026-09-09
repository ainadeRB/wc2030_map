"""Chargement et nettoyage des fichiers Excel Hôtels / Points d'intérêt."""
import re

import pandas as pd
import numpy as np
import streamlit as st

from .styling import extract_star_rating

# Colonnes attendues dans le fichier hôtels (ordre = celui fourni par le projet)
HOTEL_COLUMNS = [
    "ID", "Ville", "Ville hôte", "Nom", "Catégorie", "Nouveau classement assimilé",
    "Nouveau Statut vérifié", "PMC vérif", "Capacité act (cha.)",
    "1.VSTH", "2.TBCTH", "3. FIFA HQ", "4. FIFA VIP", "5. FIFA Venue", "6. RBC",
    "7. Com", "8. Hospi", "9. HB", "10. Media", "11. IBC", "#Chambres alloues total",
    "Date ouverture", "Date dernière réno", "Date prochaine réno",
    "Propriétaire", "Opérateur", "Latitude", "Longitude",
    "Signature Prop", "Signature Op", "Signature", "Note Booking", "Risque", "Visite",
]

ALLOCATION_COLUMNS = [
    "1.VSTH", "2.TBCTH", "3. FIFA HQ", "4. FIFA VIP", "5. FIFA Venue", "6. RBC",
    "7. Com", "8. Hospi", "9. HB", "10. Media", "11. IBC",
]

NUMERIC_COLUMNS = [
    "PMC vérif", "Capacité act (cha.)", "#Chambres alloues total", "Note Booking",
] + ALLOCATION_COLUMNS

DATE_COLUMNS = ["Date ouverture", "Date dernière réno", "Date prochaine réno"]

# Bornes larges du Maroc, pour repérer des coordonnées visiblement fausses
MOROCCO_BOUNDS = {"lat": (20.0, 36.5), "lon": (-17.5, -0.5)}


# "1.VSTH" -> "VSTH", "3. FIFA HQ" -> "FIFA HQ" : le numéro d'ordre du
# fichier source n'a pas sa place dans un résumé lisible à l'écran.
def _strip_allocation_prefix(col: str) -> str:
    return re.sub(r"^\d+\.\s*", "", col)


def _format_allocations(row) -> "str | float":
    """Résumé lisible ("VSTH : 12 · FIFA VIP : 4") des groupes auxquels un
    hôtel a des chambres allouées, à partir des colonnes ALLOCATION_COLUMNS
    déjà présentes dans le fichier — ne montre que les groupes avec une
    valeur non nulle, plutôt que d'obliger à maintenir une colonne à part
    dans Excel."""
    parts = [
        f"{_strip_allocation_prefix(col)} : {int(row[col])}"
        for col in ALLOCATION_COLUMNS
        if pd.notna(row[col]) and row[col] > 0
    ]
    return " · ".join(parts) if parts else np.nan


def _find_col(columns, *candidates):
    lowered = {c.lower().strip(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


@st.cache_data(show_spinner=False)
def load_hotels(file_or_path) -> pd.DataFrame:
    df = pd.read_excel(file_or_path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    # colonnes manquantes -> créées vides, pour que le reste de l'app soit robuste
    for col in HOTEL_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", ".", regex=False).str.replace(r"[^\d.\-]", "", regex=True),
            errors="coerce",
        )

    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)

    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")

    lat_ok = df["Latitude"].between(*MOROCCO_BOUNDS["lat"])
    lon_ok = df["Longitude"].between(*MOROCCO_BOUNDS["lon"])
    df["Géolocalisé"] = lat_ok & lon_ok

    df["Étoiles (estimées)"] = df["Nouveau classement assimilé"].apply(extract_star_rating)
    df["Étoiles (estimées)"] = df["Étoiles (estimées)"].fillna(
        df["Catégorie"].apply(extract_star_rating)
    )

    if df["#Chambres alloues total"].isna().all() or (df["#Chambres alloues total"] == 0).all():
        df["#Chambres alloues total"] = df[ALLOCATION_COLUMNS].sum(axis=1, min_count=1)

    df["Allocations"] = df.apply(_format_allocations, axis=1)

    for col in ["Ville", "Ville hôte", "Nom", "Catégorie", "Nouveau classement assimilé",
                "Nouveau Statut vérifié", "Propriétaire", "Opérateur", "Signature Prop",
                "Signature Op", "Signature", "Risque", "Visite", "ID"]:
        df[col] = df[col].astype(str).str.strip().replace({"nan": np.nan, "None": np.nan, "": np.nan})

    # Doublons d'ID : on garde la ligne géolocalisée en priorité (un ID avec
    # une ligne vide + une ligne renseignée ne doit pas perdre la localisation).
    if df["ID"].notna().any():
        df = (
            df.sort_values("Géolocalisé", ascending=False, kind="stable")
            .drop_duplicates(subset="ID", keep="first")
            .sort_index()
        )

    return df


def _load_poi_sheet(df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    """Convertit un onglet du fichier POI (un type de point d'intérêt par
    onglet, ex. 'Stades', 'Sites d'entraînement', 'Aéroports'...) au format
    standard Nom/Type/Ville/Latitude/Longitude. Le nom de l'onglet définit
    toujours le type (donc la couche affichée) ; la première colonne
    "métier" (ex. 'Stade', 'Aéroport'...) devient le nom du point. Une
    éventuelle colonne "Type" interne à l'onglet (ex. sous-catégorie d'un
    site d'entraînement) est conservée comme simple attribut ("Sous-type"),
    pas comme couche."""
    df.columns = [str(c).strip() for c in df.columns]
    lat_col = _find_col(df.columns, "Latitude", "Lat")
    lon_col = _find_col(df.columns, "Longitude", "Lon", "Lng")
    if not lat_col or not lon_col:
        return pd.DataFrame(columns=["Nom", "Type", "Ville", "Latitude", "Longitude"])

    city_col = _find_col(df.columns, "Ville", "Ville hôte", "City")
    inner_type_col = _find_col(df.columns, "Type", "Catégorie", "Category")
    id_col = _find_col(df.columns, "ID", "Id")
    name_col = _find_col(df.columns, "Nom", "Name")
    if not name_col:
        remaining = [c for c in df.columns if c not in {lat_col, lon_col, city_col, id_col}]
        name_col = remaining[0] if remaining else df.columns[0]

    out = pd.DataFrame()
    out["Nom"] = df[name_col].astype(str).str.strip()
    out["Type"] = sheet_name
    out["Ville"] = df[city_col] if city_col else np.nan
    out["Latitude"] = pd.to_numeric(df[lat_col], errors="coerce")
    out["Longitude"] = pd.to_numeric(df[lon_col], errors="coerce")
    if inner_type_col:
        out["Sous-type"] = df[inner_type_col]

    extra_cols = [c for c in df.columns if c not in {name_col, lat_col, lon_col, city_col, inner_type_col}]
    for c in extra_cols:
        out[c] = df[c]

    return out.dropna(subset=["Latitude", "Longitude"])


@st.cache_data(show_spinner=False)
def load_pois(file_or_path) -> pd.DataFrame:
    xls = pd.ExcelFile(file_or_path)
    sheets = []
    for sheet_name in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet_name, dtype=str)
        sheets.append(_load_poi_sheet(raw, sheet_name.strip()))

    if not sheets:
        return pd.DataFrame(columns=["Nom", "Type", "Ville", "Latitude", "Longitude"])
    return pd.concat(sheets, ignore_index=True)
