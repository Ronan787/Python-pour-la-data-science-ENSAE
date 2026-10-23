"""
OBJECTIF
    Récupérer les données carroyées à 200 m de Filosofi 2021 (INSEE : revenus,
    pauvreté et niveau de vie) au format GeoPackage, avec les géométries des carreaux.

SOURCE
    https://www.insee.fr/fr/statistiques/8735162
    Le zip (Filosofi2021_carreaux_200m_gpkg.zip) a été téléchargé une fois pour toutes
    depuis le site de l'INSEE et déposé dans le dossier diffusion du bucket S3.

ÉTAPES
    1. download_from_s3()  copie le zip depuis S3 dans insee_filosofi/.
                           Si le zip est déjà présent, il n'est pas recopié.
    2. extract_zip()       décompresse le zip et renvoie la liste des .gpkg.

REMARQUES
    Le zip contient les données des trois zones du champ (France métropolitaine,
    Martinique, La Réunion). Les carreaux de moins de 11 ménages fiscaux ont des
    valeurs imputées : la colonne i_est_200 vaut alors 1.

UTILISATION
    Configurer le paramètre S3_BUCKET en renseignant le nom de son bucket S3.
    pip install -r requirements.txt
    python scripts/data_extraction_insee.py
"""

import zipfile
from pathlib import Path

import s3fs

# --- Paramètres -------------------------------------------------------------

S3_BUCKET = "cbouyer"
ZIP_NAME = "Filosofi2021_carreaux_200m_gpkg.zip"  # nom du zip dans S3

S3_PATH = f"{S3_BUCKET}/diffusion/{ZIP_NAME}"
OUT_DIR = Path("insee_filosofi")
ZIP_PATH = OUT_DIR / ZIP_NAME


# --- Récupération depuis S3 -------------------------------------------------


def download_from_s3() -> None:
    """
    Copie le zip depuis le dossier diffusion du bucket S3 vers OUT_DIR.
    """
    if S3_BUCKET == "nom_du_bucket":
        raise ValueError("Renseignez le nom du bucket dans S3_BUCKET.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if ZIP_PATH.exists():
        print(f"{ZIP_PATH} déjà présent, pas de nouvelle copie.")
        return

    fs = s3fs.S3FileSystem(
        client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
    )
    tmp = ZIP_PATH.with_suffix(".tmp")
    fs.get(S3_PATH, str(tmp))
    tmp.replace(ZIP_PATH)   # le .zip n'existe jamais à l'état partiel
    print(f"Fichier copié depuis s3://{S3_PATH}")


# --- Décompression ----------------------------------------------------------


def extract_zip() -> list[Path]:
    """
    Décompresse le zip dans OUT_DIR et renvoie la liste des fichiers .gpkg.
    """
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(OUT_DIR)

    gpkg_files = sorted(OUT_DIR.rglob("*.gpkg"))
    print(f"Fichiers extraits : {[p.name for p in gpkg_files]}")
    return gpkg_files


# ----- Exécution du script --------------------------------------------
# Récupère le zip depuis S3 puis le décompresse.

if __name__ == "__main__":
    download_from_s3()
    extract_zip()