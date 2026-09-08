"""Chargement et nettoyage des fichiers Excel Hôtels / Points d'intérêt."""
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

    for col in ["Ville", "Ville hôte", "Nom", "Catégorie", "Nouveau classement assimilé",
                "Nouveau Statut vérifié", "Propriétaire", "Opérateur", "Signature Prop",
                "Signature Op", "Signature", "Risque", "Visite", "ID"]:
        df[col] = df[col].astype(str).str.strip().replace({"nan": np.nan, "None": np.nan, "": np.nan})

    return df


@st.cache_data(show_spinner=False)
def load_pois(file_or_path) -> pd.DataFrame:
    df = pd.read_excel(file_or_path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    name_col = _find_col(df.columns, "Nom", "Name", "Site") or df.columns[0]
    type_col = _find_col(df.columns, "Type", "Catégorie", "Category")
    city_col = _find_col(df.columns, "Ville", "Ville hôte", "City")
    lat_col = _find_col(df.columns, "Latitude", "Lat")
    lon_col = _find_col(df.columns, "Longitude", "Lon", "Lng")

    out = pd.DataFrame()
    out["Nom"] = df[name_col]
    out["Type"] = df[type_col] if type_col else "Point d'intérêt"
    out["Ville"] = df[city_col] if city_col else np.nan
    out["Latitude"] = pd.to_numeric(df[lat_col], errors="coerce") if lat_col else np.nan
    out["Longitude"] = pd.to_numeric(df[lon_col], errors="coerce") if lon_col else np.nan

    extra_cols = [c for c in df.columns if c not in {name_col, type_col, city_col, lat_col, lon_col}]
    for c in extra_cols:
        out[c] = df[c]

    out["Type"] = out["Type"].fillna("Point d'intérêt").astype(str).str.strip()
    out = out.dropna(subset=["Latitude", "Longitude"])
    return out
