"""Coupe du Monde 2030 – Maroc : cartographie interactive des hôtels
et points d'intérêt (stades, sites d'entraînement, aéroports...).

Lancer avec : streamlit run app.py
"""
from io import BytesIO
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src.data_loader import ALLOCATION_COLUMNS, load_hotels, load_pois
from src.geo import bounds_for, haversine_km
from src.styling import (
    build_palette_map,
    scale_radius,
    DEFAULT_COLOR,
    POI_TYPE_COLORS,
    POI_TYPE_ICON,
    STAR_COLORS,
)

st.set_page_config(page_title="WC2030 Maroc – Carte Hôtels", page_icon="🗺️", layout="wide")

DATA_DIR = Path("data")
DEFAULT_HOTELS = DATA_DIR / "sample_hotels.xlsx"
DEFAULT_POIS = DATA_DIR / "sample_poi.xlsx"
# Fichiers réels de l'utilisateur : écrasés à chaque upload, conservés entre les sessions.
HOTELS_PERSIST_PATH = DATA_DIR / "hotels.xlsx"
POIS_PERSIST_PATH = DATA_DIR / "poi.xlsx"

COLOR_MODES = {
    "Nouveau classement assimilé (étoiles)": "stars",
    "Nouveau classement assimilé (valeur brute)": "Nouveau classement assimilé",
    "Catégorie": "Catégorie",
    "Nouveau Statut vérifié": "Nouveau Statut vérifié",
    "Ville hôte": "Ville hôte",
    "Signature": "Signature",
    "Risque": "Risque",
    "Visite": "Visite",
}

# Fonds de carte gratuits, sans clé API (CARTO exige désormais une clé,
# on utilise donc Esri/OpenStreetMap/OpenTopoMap qui restent libres d'accès).
BASEMAPS = {
    "Standard (rues, recommandé)": {"tiles": "OpenStreetMap", "attr": None, "max_zoom": 19},
    "Clair épuré (gris)": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles &copy; Esri — Esri, DeLorme, NAVTEQ",
        "max_native_zoom": 16, "max_zoom": 19,
    },
    "Satellite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles &copy; Esri — Esri, Maxar, Earthstar Geographics",
        "max_zoom": 19,
    },
    "Relief": {
        "tiles": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "attr": "OpenTopoMap",
        "max_native_zoom": 17, "max_zoom": 19,
    },
}

HOTEL_INFO_FIELDS = [
    "Nom", "Ville hôte", "Ville", "Catégorie", "Nouveau classement assimilé",
    "Nouveau Statut vérifié", "Capacité act (cha.)", "#Chambres alloues total",
    "PMC vérif", "Propriétaire", "Opérateur", "Signature", "Signature Prop",
    "Signature Op", "Risque", "Visite", "Note Booking", "ID",
]
DEFAULT_TOOLTIP_FIELDS = [
    "Nom", "Ville hôte", "Nouveau classement assimilé", "Capacité act (cha.)", "Nouveau Statut vérifié",
]

# Rayon (px) des hôtels sans valeur pour le champ de taille choisi : fixe,
# non affecté par le curseur d'échelle des bulles.
BASE_DOT_RADIUS = 3

CATEGORICAL_FILTERS = [
    ("Ville hôte", "Ville hôte"),
    ("Ville", "Ville"),
    ("Catégorie", "Catégorie"),
    ("Nouveau classement assimilé", "Nouveau classement assimilé"),
    ("Nouveau Statut vérifié", "Nouveau Statut vérifié"),
    ("Propriétaire", "Propriétaire"),
    ("Opérateur", "Opérateur"),
    ("Signature Prop", "Signature Prop"),
    ("Signature Op", "Signature Op"),
    ("Signature global", "Signature"),
    ("Risque", "Risque"),
    ("Visite", "Visite"),
]

NUMERIC_FILTERS = [
    ("Capacité (chambres)", "Capacité act (cha.)"),
    ("Prix moyen chambre (PMC vérif)", "PMC vérif"),
    ("Chambres allouées (total)", "#Chambres alloues total"),
    ("Note Booking", "Note Booking"),
]

DATE_FILTERS = [
    ("Date d'ouverture", "Date ouverture"),
    ("Date dernière rénovation", "Date dernière réno"),
    ("Date prochaine rénovation", "Date prochaine réno"),
]


def init_state():
    st.session_state.setdefault("ref_point", None)
    st.session_state.setdefault("radius_km", 15)
    st.session_state.setdefault("distance_filter_on", False)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Hôtels filtrés")
    return buffer.getvalue()


def get_color_map(color_mode, values):
    """Palette éditable par l'utilisateur pour le critère de couleur choisi,
    conservée en session pour ne pas revenir aux couleurs par défaut à
    chaque interaction."""
    state_key = f"colormap_{color_mode}"
    if state_key not in st.session_state:
        if color_mode == "stars":
            st.session_state[state_key] = {v: STAR_COLORS.get(int(v), DEFAULT_COLOR) for v in values}
        else:
            st.session_state[state_key] = build_palette_map(values)
    else:
        for v in values:
            if v not in st.session_state[state_key]:
                st.session_state[state_key][v] = DEFAULT_COLOR
    return st.session_state[state_key]


def build_tooltip_html(row, fields):
    parts = []
    for field in fields:
        val = row.get(field)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            val = f"{val:,.0f}".replace(",", " ")
        if field == "Nom":
            parts.insert(0, f"<b>{val}</b>")
        else:
            parts.append(f"{field} : {val}")
    if not parts:
        parts = [str(row.get("Nom", ""))]
    return "<br>".join(parts)


@st.cache_data(show_spinner=False)
def _cached_hotels(file_bytes_or_path, is_upload):
    if is_upload:
        return load_hotels(BytesIO(file_bytes_or_path))
    return load_hotels(file_bytes_or_path)


@st.cache_data(show_spinner=False)
def _cached_pois(file_bytes_or_path, is_upload):
    if is_upload:
        return load_pois(BytesIO(file_bytes_or_path))
    return load_pois(file_bytes_or_path)


def _load_with_persistence(uploader_label, uploader_key, persist_path: Path, default_path: Path, loader_bytes_fn):
    """Widget d'upload qui écrit le fichier déposé sur disque (écrasé à chaque
    nouvel upload), pour qu'il soit rechargé automatiquement aux prochaines
    sessions sans avoir à le redéposer. Retourne (dataframe, message d'état).
    À appeler dans un bloc `with` (container/sidebar) pour que les widgets
    s'affichent au bon endroit."""
    uploaded = st.file_uploader(uploader_label, type=["xlsx", "xls"], key=uploader_key)

    if uploaded is not None:
        persist_path.parent.mkdir(parents=True, exist_ok=True)
        persist_path.write_bytes(uploaded.getvalue())
        st.success(f"Fichier enregistré → `{persist_path}`")

    if persist_path.exists():
        mtime = pd.Timestamp.fromtimestamp(persist_path.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        df = loader_bytes_fn(persist_path.read_bytes(), True)
        status = f"✅ Fichier réel chargé (`{persist_path.name}`, mis à jour le {mtime})"
        if st.button("🗑️ Revenir aux données de démo", key=f"reset_{uploader_key}"):
            persist_path.unlink(missing_ok=True)
            st.rerun()
    else:
        df = loader_bytes_fn(default_path, False)
        status = f"ℹ️ Données de démonstration (`{default_path.name}`) — dépose ton fichier ci-dessus pour le remplacer durablement."

    return df, status


def sidebar_data_sources():
    with st.sidebar.container(border=True):
        st.markdown("### 📂 Données")
        hotels_df, hotels_status = _load_with_persistence(
            "Fichier hôtels (Excel)", "hotel_upload", HOTELS_PERSIST_PATH, DEFAULT_HOTELS, _cached_hotels
        )
        st.caption(hotels_status)

        try:
            pois_df, pois_status = _load_with_persistence(
                "Fichier points d'intérêt (Excel)", "poi_upload", POIS_PERSIST_PATH, DEFAULT_POIS, _cached_pois
            )
            st.caption(pois_status)
        except Exception:
            pois_df = pd.DataFrame(columns=["Nom", "Type", "Ville", "Latitude", "Longitude"])
            st.warning("Impossible de lire le fichier de points d'intérêt.")

    return hotels_df, pois_df


def sidebar_filters(df: pd.DataFrame):
    filtered = df.copy()

    with st.sidebar.container(border=True):
        st.markdown("### 🔍 Filtres")

        with st.expander("🏙️ Localisation & classification", expanded=True):
            for label, col in CATEGORICAL_FILTERS[:5]:
                options = sorted(filtered[col].dropna().unique().tolist())
                if not options:
                    continue
                chosen = st.multiselect(label, options, default=[], key=f"filt_{col}")
                if chosen:
                    filtered = filtered[filtered[col].isin(chosen)]

        with st.expander("✍️ Parties prenantes & statut"):
            for label, col in CATEGORICAL_FILTERS[5:]:
                options = sorted(filtered[col].dropna().unique().tolist())
                if not options:
                    continue
                chosen = st.multiselect(label, options, default=[], key=f"filt_{col}")
                if chosen:
                    filtered = filtered[filtered[col].isin(chosen)]

        with st.expander("🔢 Capacité, prix & notes"):
            for label, col in NUMERIC_FILTERS:
                series = filtered[col].dropna()
                if series.empty:
                    continue
                vmin, vmax = float(series.min()), float(series.max())
                if vmin == vmax:
                    continue
                lo, hi = st.slider(label, min_value=float(np.floor(vmin)), max_value=float(np.ceil(vmax)),
                                    value=(float(np.floor(vmin)), float(np.ceil(vmax))), key=f"filt_{col}")
                filtered = filtered[filtered[col].between(lo, hi) | filtered[col].isna()]

        with st.expander("📅 Dates"):
            for label, col in DATE_FILTERS:
                series = filtered[col].dropna()
                if series.empty:
                    continue
                dmin, dmax = series.min().date(), series.max().date()
                if dmin == dmax:
                    continue
                lo, hi = st.date_input(label, value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"filt_{col}")
                if isinstance(lo, tuple):
                    lo, hi = lo
                filtered = filtered[(filtered[col].dt.date.between(lo, hi)) | filtered[col].isna()]

        with st.expander("🔎 Recherche"):
            search = st.text_input("Nom de l'hôtel contient...", "")
            only_geo = st.checkbox("Afficher uniquement les hôtels géolocalisés", value=True)
            if search:
                filtered = filtered[filtered["Nom"].str.contains(search, case=False, na=False)]
            if only_geo:
                filtered = filtered[filtered["Géolocalisé"]]

    return filtered


def sidebar_map_settings(hotels_df: pd.DataFrame, pois_df: pd.DataFrame):
    with st.sidebar.container(border=True):
        st.markdown("### 🗺️ Carte")

        basemap_choice = st.selectbox("Fond de carte", list(BASEMAPS.keys()), index=0)

        st.markdown("**Couches**")
        show_hotels = st.checkbox("Hôtels", value=True)
        poi_types = sorted(pois_df["Type"].dropna().unique().tolist())
        active_poi_types = []
        if poi_types:
            st.caption("Points d'intérêt")
            for t in poi_types:
                if st.checkbox(f"　{t}", value=True, key=f"poi_{t}"):
                    active_poi_types.append(t)

        st.markdown("**Taille des bulles**")
        size_options = dict(NUMERIC_FILTERS)
        size_label = st.selectbox("Taille selon", list(size_options.keys()), index=0, key="size_mode")
        size_col = size_options[size_label]
        size_scale = st.slider("Échelle des bulles", min_value=0.4, max_value=3.0, value=1.0, step=0.1, key="size_scale")
        st.caption("Un hôtel sans valeur pour ce champ garde un petit point de taille fixe, quelle que soit l'échelle.")

        st.markdown("**Style des bulles**")
        color_label = st.selectbox("Couleur selon", list(COLOR_MODES.keys()), index=0)
        color_mode = COLOR_MODES[color_label]

        if color_mode == "stars":
            values = sorted(v for v in hotels_df["Étoiles (estimées)"].dropna().unique().tolist())
        elif color_mode in hotels_df.columns:
            values = sorted(hotels_df[color_mode].dropna().unique().tolist())
        else:
            values = []
        color_map = get_color_map(color_mode, values)

        with st.expander("🎨 Personnaliser les couleurs"):
            if values:
                for v in values:
                    label = f"{int(v)} ★" if color_mode == "stars" else str(v)
                    color_map[v] = st.color_picker(label, color_map.get(v, DEFAULT_COLOR), key=f"cp_{color_mode}_{v}")
            else:
                st.caption("Aucune valeur à colorer pour ce critère.")

        st.markdown("**Infos au survol**")
        tooltip_fields = st.multiselect(
            "Champs affichés en survolant un hôtel", HOTEL_INFO_FIELDS,
            default=DEFAULT_TOOLTIP_FIELDS, key="tooltip_fields",
        )

    return show_hotels, active_poi_types, color_mode, color_label, color_map, basemap_choice, tooltip_fields, size_col, size_label, size_scale


def sidebar_distance_filter():
    with st.sidebar.container(border=True):
        st.markdown("### 📍 Filtre par distance")
        st.caption("Clique sur la carte pour poser un point de référence, puis ajuste le rayon.")
        st.session_state["distance_filter_on"] = st.checkbox(
            "Activer le filtre par distance", value=st.session_state["distance_filter_on"]
        )
        st.session_state["radius_km"] = st.slider(
            "Rayon (km)", min_value=1, max_value=150, value=st.session_state["radius_km"]
        )
        col1, col2 = st.columns(2)
        with col1:
            lat_in = st.number_input("Latitude", value=float(st.session_state["ref_point"][0]) if st.session_state["ref_point"] else 0.0, format="%.6f")
        with col2:
            lon_in = st.number_input("Longitude", value=float(st.session_state["ref_point"][1]) if st.session_state["ref_point"] else 0.0, format="%.6f")
        apply_manual = st.button("Utiliser ces coordonnées")
        if apply_manual and (lat_in != 0.0 or lon_in != 0.0):
            st.session_state["ref_point"] = (lat_in, lon_in)
        if st.button("Réinitialiser le point"):
            st.session_state["ref_point"] = None
            st.session_state["distance_filter_on"] = False


def build_map(hotels_df, pois_df, show_hotels, active_poi_types, color_mode, color_map, basemap_choice, tooltip_fields, size_col, size_scale):
    center = [31.7917, -7.0926]
    zoom = 5.4
    all_points = hotels_df[["Latitude", "Longitude"]].dropna() if show_hotels else pd.DataFrame(columns=["Latitude", "Longitude"])
    b = bounds_for(all_points) if not all_points.empty else None

    basemap = BASEMAPS.get(basemap_choice, next(iter(BASEMAPS.values())))
    m = folium.Map(location=center, zoom_start=zoom, tiles=None, prefer_canvas=True,
                    max_zoom=basemap.get("max_zoom", 19))
    tile_kwargs = {"tiles": basemap["tiles"], "name": basemap_choice, "max_zoom": basemap.get("max_zoom", 19)}
    if basemap["attr"]:
        tile_kwargs["attr"] = basemap["attr"]
    if basemap.get("max_native_zoom"):
        tile_kwargs["max_native_zoom"] = basemap["max_native_zoom"]
    folium.TileLayer(**tile_kwargs).add_to(m)

    if show_hotels and not hotels_df.empty:
        size_series = hotels_df[size_col].dropna() if size_col in hotels_df.columns else pd.Series(dtype=float)
        size_min, size_max = (size_series.min(), size_series.max()) if not size_series.empty else (0, 1)
        hotel_layer = folium.FeatureGroup(name="Hôtels", show=True)
        for _, row in hotels_df.iterrows():
            if pd.isna(row["Latitude"]) or pd.isna(row["Longitude"]):
                continue
            size_value = row.get(size_col)
            has_size = size_value is not None and not (isinstance(size_value, float) and pd.isna(size_value))
            radius = scale_radius(size_value, size_min, size_max) * size_scale if has_size else BASE_DOT_RADIUS
            cat_value = row["Étoiles (estimées)"] if color_mode == "stars" else row.get(color_mode)
            has_value = cat_value is not None and not (isinstance(cat_value, float) and pd.isna(cat_value))
            color = color_map.get(cat_value, DEFAULT_COLOR) if has_value else DEFAULT_COLOR

            popup_html = f"""
            <b>{row['Nom']}</b><br>
            Ville hôte : {row['Ville hôte']}<br>
            Catégorie : {row['Catégorie']}<br>
            Classement : {row['Nouveau classement assimilé']}<br>
            Statut : {row['Nouveau Statut vérifié']}<br>
            Capacité : {row['Capacité act (cha.)']:.0f} ch.<br>
            Chambres allouées : {row['#Chambres alloues total']:.0f}<br>
            PMC : {row['PMC vérif']:.0f} MAD<br>
            Signature : {row['Signature']}<br>
            Risque : {row['Risque']}<br>
            Note Booking : {row['Note Booking']}
            """
            folium.CircleMarker(
                location=[row["Latitude"], row["Longitude"]],
                radius=radius,
                color=color,
                weight=1.5,
                fill=True,
                fill_color=color,
                fill_opacity=0.75,
                tooltip=folium.Tooltip(build_tooltip_html(row, tooltip_fields), sticky=True),
                popup=folium.Popup(popup_html, max_width=280),
            ).add_to(hotel_layer)
        hotel_layer.add_to(m)

    for poi_type in active_poi_types:
        subset = pois_df[pois_df["Type"] == poi_type]
        if subset.empty:
            continue
        layer = folium.FeatureGroup(name=poi_type, show=True)
        color = POI_TYPE_COLORS.get(poi_type, "#333333")
        icon = POI_TYPE_ICON.get(poi_type, "map-marker")
        for _, row in subset.iterrows():
            folium.Marker(
                location=[row["Latitude"], row["Longitude"]],
                tooltip=f"{row['Nom']} ({poi_type})",
                icon=folium.Icon(color="lightgray", icon_color=color, icon=icon, prefix="fa"),
            ).add_to(layer)
        layer.add_to(m)

    ref_point = st.session_state.get("ref_point")
    if ref_point and st.session_state.get("distance_filter_on"):
        radius_km = st.session_state["radius_km"]
        folium.Marker(location=list(ref_point), icon=folium.Icon(color="red", icon="crosshairs", prefix="fa"),
                       tooltip="Point de référence").add_to(m)
        folium.Circle(location=list(ref_point), radius=radius_km * 1000, color="#d62728",
                       fill=True, fill_opacity=0.05, weight=2).add_to(m)
    elif ref_point:
        folium.Marker(location=list(ref_point), icon=folium.Icon(color="red", icon="crosshairs", prefix="fa"),
                       tooltip="Point de référence (clic)").add_to(m)

    if b:
        m.fit_bounds(b)

    return m


def main():
    init_state()
    st.title("🗺️ Coupe du Monde 2030 – Maroc — Cartographie Hôtels & Sites")
    st.caption(
        "Carte interactive type Kepler.gl : couches activables, filtres sur toutes les colonnes, "
        "bulles proportionnelles à la capacité et colorées par qualité, filtre par distance en cliquant sur la carte."
    )

    hotels_df, pois_df = sidebar_data_sources()
    show_hotels, active_poi_types, color_mode, color_label, color_map, basemap_choice, tooltip_fields, size_col, size_label, size_scale = sidebar_map_settings(hotels_df, pois_df)
    filtered_df = sidebar_filters(hotels_df)
    sidebar_distance_filter()

    if st.session_state["distance_filter_on"] and st.session_state["ref_point"]:
        lat0, lon0 = st.session_state["ref_point"]
        dist = haversine_km(lat0, lon0, filtered_df["Latitude"].values, filtered_df["Longitude"].values)
        filtered_df = filtered_df.assign(**{"Distance (km)": dist})
        filtered_df = filtered_df[filtered_df["Distance (km)"] <= st.session_state["radius_km"]]

    n_total = len(hotels_df)
    n_geo = int(hotels_df["Géolocalisé"].sum())
    n_shown = len(filtered_df)

    kpi_cols = st.columns(5)
    kpi_cols[0].metric("Hôtels (base)", f"{n_total}")
    kpi_cols[1].metric("Géolocalisés", f"{n_geo}", help="Nombre d'hôtels avec coordonnées valides dans la base")
    kpi_cols[2].metric("Hôtels affichés", f"{n_shown}")
    kpi_cols[3].metric("Chambres (capacité totale)", f"{int(filtered_df['Capacité act (cha.)'].sum(skipna=True)):,}".replace(",", " "))
    kpi_cols[4].metric("Chambres allouées", f"{int(filtered_df['#Chambres alloues total'].sum(skipna=True)):,}".replace(",", " "))

    fmap = build_map(filtered_df, pois_df, show_hotels, active_poi_types, color_mode, color_map, basemap_choice, tooltip_fields, size_col, size_scale)
    map_state = st_folium(fmap, use_container_width=True, height=720, key="main_map",
                           returned_objects=["last_clicked"])
    if map_state and map_state.get("last_clicked"):
        clicked = map_state["last_clicked"]
        new_point = (clicked["lat"], clicked["lng"])
        if new_point != st.session_state.get("ref_point"):
            st.session_state["ref_point"] = new_point
            st.rerun()

    st.markdown(f"**Légende couleur : {color_label}**")
    legend_items = [
        (f"{int(v)} ★" if color_mode == "stars" else str(v), c)
        for v, c in sorted(color_map.items(), key=lambda kv: str(kv[0]))
    ]
    legend_html = " &nbsp; ".join(
        f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{c};margin-right:4px;"></span>{lbl}'
        for lbl, c in legend_items
    )
    st.markdown(legend_html, unsafe_allow_html=True)

    st.subheader("📋 Liste des hôtels filtrés")
    display_cols = [c for c in [
        "ID", "Nom", "Ville hôte", "Ville", "Catégorie", "Nouveau classement assimilé",
        "Nouveau Statut vérifié", "Capacité act (cha.)", "#Chambres alloues total",
        "PMC vérif", "Signature", "Risque", "Visite", "Note Booking", "Distance (km)",
    ] if c in filtered_df.columns]
    st.dataframe(filtered_df[display_cols].sort_values(display_cols[0]), width="stretch", height=350)

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.download_button(
            "⬇️ Télécharger la sélection (Excel)",
            data=to_excel_bytes(filtered_df[display_cols]),
            file_name="hotels_filtres_wc2030.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with dl_col2:
        st.download_button(
            "⬇️ Télécharger la sélection (CSV)",
            data=filtered_df[display_cols].to_csv(index=False).encode("utf-8-sig"),
            file_name="hotels_filtres_wc2030.csv",
            mime="text/csv",
        )

    with st.expander("ℹ️ À propos de cet outil / prochaines étapes"):
        st.markdown(
            """
            - **Carte** : choisis le fond de carte (clair épuré, standard, satellite, relief) et active/désactive les hôtels et chaque type de point d'intérêt, dans le bloc "🗺️ Carte" de la barre latérale.
            - **Filtres** : tous les champs du fichier hôtels sont filtrables, regroupés dans le bloc "🔍 Filtres" (localisation, classification, capacité, prix, dates, parties prenantes, signature, risque, visite).
            - **Bulles** : taille = champ numérique au choix (capacité, chambres allouées, PMC, note Booking), ajustable avec le curseur "Échelle des bulles" — un hôtel sans valeur pour ce champ garde un point fixe, non affecté par le curseur ; couleur = critère choisi (classement, catégorie, statut, ville hôte, signature, risque, visite), avec une couleur personnalisable pour chaque valeur via "🎨 Personnaliser les couleurs".
            - **Survol** : choisis les informations affichées au survol d'un hôtel dans "Infos au survol" (le clic affiche toujours la fiche complète).
            - **Filtre par distance** : clique sur la carte (ou saisis des coordonnées) pour poser un point de référence, active le filtre et ajuste le rayon en km. Le calcul actuel est à vol d'oiseau ; un calcul en **temps de trajet réel** (via un moteur de routage type OSRM) pourra être ajouté en connectant une API de routage.
            - **Données** : dépose tes fichiers Excel réels (hôtels + POI) dans la barre latérale — l'app détecte automatiquement les colonnes. En attendant, des données de démonstration sont utilisées.
            """
        )


if __name__ == "__main__":
    main()
