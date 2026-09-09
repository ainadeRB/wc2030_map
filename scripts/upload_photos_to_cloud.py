"""Envoie les photos locales (data/photos/<id_hotel>/*.jpg) vers Cloudinary
et écrit data/photos_manifest.json (hotel_id -> liste d'URLs) : c'est ce
fichier, lui bien versionné dans git, qui permet à la version hébergée
(Streamlit Community Cloud, sans disque persistant) d'afficher les mêmes
photos que la version locale — voir src/photos.py.

Usage (à lancer UNE FOIS localement, là où sont les vraies photos, puis à
nouveau à chaque fois que des photos sont ajoutées/modifiées) :

    pip install cloudinary
    export CLOUDINARY_CLOUD_NAME=...
    export CLOUDINARY_API_KEY=...
    export CLOUDINARY_API_SECRET=...
    python3 scripts/upload_photos_to_cloud.py

Identifiants gratuits sur https://cloudinary.com (Dashboard, une fois
inscrit) — jamais à coller dans le code ni sur GitHub, seulement dans ces
variables d'environnement locales.

Une fois terminé :
    git add data/photos_manifest.json
    git commit -m "Met à jour les photos hébergées"
    git push

Idempotent : ne ré-envoie pas une photo déjà présente (comparaison par nom
de fichier + empreinte du contenu, mémorisée dans
data/.photos_upload_cache.json, jamais versionné), donc un ré-lancement
après ajout ou modification de quelques photos ne renvoie que celles-là.
--force ignore ce cache et tout ré-envoie."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    print("Installe d'abord la bibliothèque : pip install cloudinary", file=sys.stderr)
    sys.exit(1)

import os  # noqa: E402

from src.photos import IMAGE_EXTENSIONS, PHOTOS_DIR, PHOTOS_MANIFEST_PATH  # noqa: E402

UPLOAD_CACHE_PATH = Path("data") / ".photos_upload_cache.json"


def _configure_cloudinary():
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    if not (cloud_name and api_key and api_secret):
        print(
            "Variables d'environnement manquantes : CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET (voir le Dashboard sur "
            "cloudinary.com).",
            file=sys.stderr,
        )
        sys.exit(1)
    cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Ré-envoie toutes les photos, même déjà présentes")
    args = parser.parse_args()

    _configure_cloudinary()
    if not PHOTOS_DIR.is_dir():
        print(f"Dossier introuvable : {PHOTOS_DIR}", file=sys.stderr)
        sys.exit(1)

    # Cache "riche" (nom + taille + url), jamais versionné, qui permet de
    # savoir quoi sauter d'un lancement à l'autre. data/photos_manifest.json
    # (versionné) n'a lui que les URLs, régénéré en fin de script.
    upload_cache = {} if args.force else _load_json(UPLOAD_CACHE_PATH)

    n_uploaded, n_skipped = 0, 0
    for hotel_dir in sorted(PHOTOS_DIR.iterdir()):
        if not hotel_dir.is_dir():
            continue
        hotel_id = hotel_dir.name
        files = sorted(f for f in hotel_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)
        if not files:
            continue

        existing_by_name = {e["filename"]: e for e in upload_cache.get(hotel_id, [])}
        entries = []
        for f in files:
            content_hash = hashlib.md5(f.read_bytes()).hexdigest()
            prior = existing_by_name.get(f.name)
            if prior and prior.get("hash") == content_hash:
                entries.append(prior)
                n_skipped += 1
                continue
            public_id = f"hotels/{hotel_id}/{f.stem}"
            result = cloudinary.uploader.upload(str(f), public_id=public_id, overwrite=True)
            entries.append({"filename": f.name, "hash": content_hash, "url": result["secure_url"]})
            n_uploaded += 1
            print(f"  {hotel_id}/{f.name} -> {result['secure_url']}")

        upload_cache[hotel_id] = entries

    UPLOAD_CACHE_PATH.write_text(json.dumps(upload_cache, indent=2, ensure_ascii=False))

    manifest = {
        hotel_id: [e["url"] for e in entries]
        for hotel_id, entries in upload_cache.items()
        if entries
    }
    PHOTOS_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"\n{n_uploaded} photo(s) envoyée(s), {n_skipped} déjà à jour.")
    print(f"Manifeste écrit : {PHOTOS_MANIFEST_PATH}")
    print("Reste à faire : git add data/photos_manifest.json && git commit && git push")


if __name__ == "__main__":
    main()
