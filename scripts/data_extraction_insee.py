"""
OBJECTIF
    Télécharger les données carroyées à 200 m de Filosofi 2021 (INSEE : revenus,
    pauvreté et niveau de vie) au format csv.

SOURCE
    https://www.insee.fr/fr/statistiques/8735162
    Fichier zip en téléchargement direct (aucune clé nécessaire).

ÉTAPES
    1. download_insee()  télécharge le zip (insee_filosofi/) et le décompresse.
                         Si le zip est déjà présent, il n'est pas retéléchargé.
    2. upload_to_s3()    enregistre les fichiers csv dans S3.

REMARQUES
    Le zip contient les données des trois zones du champ (France métropolitaine,
    Martinique, La Réunion). Les carreaux de moins de 11 ménages fiscaux ont des
    valeurs imputées : la colonne i_est_200 vaut alors 1.

UTILISATION
    Configurer le paramètre S3_BUCKET en renseignant le nom de son bucket S3 pour enregistrer
    une copie des données.
    pip install -r requirements.txt
    python scripts/data_extraction_insee.py
"""

import shutil
import zipfile
from pathlib import Path

import requests
import s3fs
from requests.adapters import HTTPAdapter, Retry
from tqdm import tqdm

# --- Paramètres -------------------------------------------------------------

S3_BUCKET = "nom_du_bucket"  # A configurer !

URL = "https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip"

OUT_DIR = Path("insee_filosofi")
ZIP_PATH = OUT_DIR / "Filosofi2021_carreaux_200m_csv.zip"


# --- Session HTTP avec reprise automatique ----------------------------------


def make_session() -> requests.Session:
    """
    Crée une session HTTP réutilisable avec des tentatives automatiques
    en cas d'erreurs temporaires du serveur.
    """
    session = requests.Session()
    retries = Retry(
        total=8,  # jusqu'à 8 tentatives
        backoff_factor=2,   # délai progressif entre les tentatives
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


# --- Téléchargement ---------------------------------------------------------


def download_insee() -> list[Path]:
    """
    Télécharge le zip Filosofi 2021 (carreaux 200 m, format csv), le décompresse
    dans OUT_DIR et renvoie la liste des fichiers csv extraits.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if ZIP_PATH.exists():
        print(f"{ZIP_PATH} déjà présent, pas de nouveau téléchargement.")
    else:
        session = make_session()
        # stream=True : le fichier est lu par morceaux, sans tout charger en mémoire
        with session.get(URL, stream=True, timeout=(15, 120)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) or None
            tmp = ZIP_PATH.with_suffix(".tmp")
            with open(tmp, "wb") as f, tqdm(
                total=total, unit="B", unit_scale=True, desc="INSEE"
            ) as pbar:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    pbar.update(len(chunk))
            tmp.replace(ZIP_PATH)   # le .zip n'existe jamais à l'état partiel

    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(OUT_DIR)

    csv_files = sorted(OUT_DIR.rglob("*.csv"))
    print(f"Fichiers extraits : {[p.name for p in csv_files]}")
    return csv_files


# ---- Exportation des fichiers sur S3 ---------------------------------------


def upload_to_s3(s3_bucket, files):
    """
    Enregistre les fichiers dans le dossier diffusion du bucket défini.
    """
    if not s3_bucket or s3_bucket == "nom_du_bucket":
        print("Export S3 ignoré : renseignez le nom du bucket.")
        return

    fs = s3fs.S3FileSystem(
        client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
    )

    for path in files:
        file_path_out_s3 = f"{s3_bucket}/diffusion/{path.name}"
        with open(path, "rb") as file_in:
            with fs.open(file_path_out_s3, "wb") as file_out:
                shutil.copyfileobj(file_in, file_out)
        print(f"Fichier exporté vers s3://{file_path_out_s3}")


# ----- Exécution du script --------------------------------------------
# Lance le téléchargement puis l'export sur S3.

if __name__ == "__main__":
    csv_files = download_insee()
    upload_to_s3(S3_BUCKET, csv_files)