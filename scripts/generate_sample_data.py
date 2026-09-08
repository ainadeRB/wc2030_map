"""Génère des fichiers Excel d'exemple (data/sample_hotels.xlsx et
data/sample_poi.xlsx) avec le schéma attendu par l'application, pour pouvoir
démarrer l'outil avant de brancher les vrais fichiers du projet.

Usage : python scripts/generate_sample_data.py
"""
import random
from pathlib import Path

import numpy as np
import pandas as pd

random.seed(42)
np.random.seed(42)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

HOST_CITIES = {
    "Casablanca": (33.5731, -7.5898),
    "Rabat": (34.0209, -6.8416),
    "Marrakech": (31.6295, -7.9811),
    "Tanger": (35.7595, -5.8340),
    "Agadir": (30.4278, -9.5981),
    "Fès": (34.0331, -5.0003),
}

CATEGORIES = ["2 étoiles", "3 étoiles", "4 étoiles", "5 étoiles", "Riad", "Non classé"]
CLASSEMENT = ["2 étoiles", "3 étoiles", "4 étoiles", "5 étoiles", "5 étoiles luxe"]
STATUTS = ["Existant", "En construction", "En rénovation", "Projet validé", "À l'étude"]
SIGNATURES = ["Signé", "En cours", "Non signé", "Refusé"]
RISQUES = ["Faible", "Moyen", "Élevé", np.nan]
VISITE = ["Visité", "Non visité", "Planifié"]
OPERATEURS = ["Accor", "Marriott", "Hyatt", "Radisson", "Indépendant", "Kenzi", "IHG"]
PROPRIETAIRES = ["Groupe Alliances", "CDG Invest", "Investisseur privé", "RMA", "Palmeraie Dev"]

rows = []
hid = 1
for city, (lat0, lon0) in HOST_CITIES.items():
    n_hotels = random.randint(9, 14)
    for _ in range(n_hotels):
        lat = lat0 + np.random.normal(0, 0.045)
        lon = lon0 + np.random.normal(0, 0.045)
        capacity = int(np.random.choice([40, 60, 80, 120, 150, 200, 300, 450], p=[.12,.18,.18,.18,.14,.1,.06,.04]))
        alloc_total_target = int(capacity * np.random.uniform(0.5, 1.0))
        weights = np.random.dirichlet(np.ones(11))
        allocs = np.floor(weights * alloc_total_target).astype(int)
        geolocated = random.random() < 0.75
        rows.append({
            "ID": f"HTL-{hid:04d}",
            "Ville": city,
            "Ville hôte": city,
            "Nom": f"Hôtel {city} {random.choice(['Palace','Garden','Bay','Medina','Atlas','Ocean','Royal','Central'])} {hid}",
            "Catégorie": random.choice(CATEGORIES),
            "Nouveau classement assimilé": random.choice(CLASSEMENT),
            "Nouveau Statut vérifié": random.choice(STATUTS),
            "PMC vérif": round(np.random.uniform(45, 420), 0),
            "Capacité act (cha.)": capacity,
            "1.VSTH": allocs[0], "2.TBCTH": allocs[1], "3. FIFA HQ": allocs[2],
            "4. FIFA VIP": allocs[3], "5. FIFA Venue": allocs[4], "6. RBC": allocs[5],
            "7. Com": allocs[6], "8. Hospi": allocs[7], "9. HB": allocs[8],
            "10. Media": allocs[9], "11. IBC": allocs[10],
            "#Chambres alloues total": int(allocs.sum()),
            "Date ouverture": pd.Timestamp("2000-01-01") + pd.Timedelta(days=random.randint(0, 9000)),
            "Date dernière réno": pd.Timestamp("2015-01-01") + pd.Timedelta(days=random.randint(0, 3800)) if random.random() < 0.7 else pd.NaT,
            "Date prochaine réno": pd.Timestamp("2027-01-01") + pd.Timedelta(days=random.randint(0, 1200)) if random.random() < 0.4 else pd.NaT,
            "Propriétaire": random.choice(PROPRIETAIRES),
            "Opérateur": random.choice(OPERATEURS),
            "Latitude": round(lat, 6) if geolocated else np.nan,
            "Longitude": round(lon, 6) if geolocated else np.nan,
            "Signature Prop": random.choice(SIGNATURES),
            "Signature Op": random.choice(SIGNATURES),
            "Signature": random.choice(SIGNATURES),
            "Note Booking": round(np.random.uniform(6.5, 9.8), 1) if random.random() < 0.8 else np.nan,
            "Risque": random.choice(RISQUES),
            "Visite": random.choice(VISITE),
        })
        hid += 1

hotels_df = pd.DataFrame(rows)
hotels_df.to_excel(DATA_DIR / "sample_hotels.xlsx", index=False)
print(f"Hôtels générés : {len(hotels_df)} -> {DATA_DIR / 'sample_hotels.xlsx'}")

STADIUMS = {
    "Casablanca": "Grand Stade Hassan II",
    "Rabat": "Stade Moulay Abdellah",
    "Marrakech": "Stade de Marrakech",
    "Tanger": "Stade Ibn Batouta",
    "Agadir": "Stade Adrar",
    "Fès": "Complexe Sportif de Fès",
}

poi_rows = []
for city, (lat0, lon0) in HOST_CITIES.items():
    poi_rows.append({"Nom": STADIUMS[city], "Type": "Stade", "Ville": city,
                      "Latitude": round(lat0 + np.random.normal(0, 0.01), 6),
                      "Longitude": round(lon0 + np.random.normal(0, 0.01), 6)})
    poi_rows.append({"Nom": f"Aéroport de {city}", "Type": "Aéroport", "Ville": city,
                      "Latitude": round(lat0 + np.random.normal(0.06, 0.02), 6),
                      "Longitude": round(lon0 + np.random.normal(0.06, 0.02), 6)})
    for i in range(1, random.randint(2, 4)):
        poi_rows.append({"Nom": f"Site d'entraînement {city} #{i}", "Type": "Site d'entraînement", "Ville": city,
                          "Latitude": round(lat0 + np.random.normal(0, 0.03), 6),
                          "Longitude": round(lon0 + np.random.normal(0, 0.03), 6)})
    poi_rows.append({"Nom": f"Fan Zone {city}", "Type": "Fan Zone", "Ville": city,
                      "Latitude": round(lat0 + np.random.normal(0, 0.015), 6),
                      "Longitude": round(lon0 + np.random.normal(0, 0.015), 6)})

poi_df = pd.DataFrame(poi_rows)
poi_df.to_excel(DATA_DIR / "sample_poi.xlsx", index=False)
print(f"POI générés : {len(poi_df)} -> {DATA_DIR / 'sample_poi.xlsx'}")
