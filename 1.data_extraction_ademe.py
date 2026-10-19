"""
OBJECTIF
    Télécharger le jeu de données ADEME « DPE Logements (avant juillet 2021) »
    et l'enregistrer au format parquet.

SOURCE
    API Data Fair de data.ademe.fr (aucune clé nécessaire).

ÉTAPES
    1. download()     Télécharge par pages de API_PAGE_SIZE lignes, regroupées en
                      fichiers intermédiaires de ROWS_PER_FILE lignes
                      (dpe_ademe/dpe_parts/part_XXXXX.parquet).
    2. merge_parts()  Fusionne les fichiers intermédiaires en dpe_ademe/dpe.parquet.
    3. enregistre le fichier dans S3

CHECKPOINT
    Permet une relance après interruption. En cas d'interruption, relancer le script
    directement. Le checkpoint n'est écrit qu'après l'écriture complète d'un fichier
    intermédiaire : au plus ROWS_PER_FILE lignes sont retéléchargées, sans doublon.
    Si TYPES, SORT_FIELD ou API_PAGE_SIZE sont modifiés, supprimer d'abord le dossier
    dpe_ademe/ avant de relancer.

UTILISATION
    pip install -r requirements.txt
    python 1.data_extraction_ademe.py
"""

import json
import shutil
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
import s3fs
from requests.adapters import HTTPAdapter, Retry
from tqdm import tqdm

# --- Paramètres -------------------------------------------------------------
URL = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe-france/lines"
API_PAGE_SIZE = 10_000       # lignes par requête
ROWS_PER_FILE = 100_000       # lignes par fichier intermédiaire
SORT_FIELD = "numero_dpe"    # identifiant unique, nécessaire à la pagination

OUT_DIR = Path("dpe_ademe")
PARTS_DIR = OUT_DIR / "dpe_parts"          # fichiers intermédiaires
FINAL_PATH = OUT_DIR / "dpe.parquet"       # fichier final (dossier de travail)
CHECKPOINT = OUT_DIR / "dpe.checkpoint.json"  # fichier JSON qui mémorise l'état du téléchargement.

TYPES = {
    "numero_dpe": pa.string(),
    "date_etablissement_dpe": pa.date32(),            # format date
    "geo_adresse": pa.string(),
    "code_insee_commune_actualise": pa.string(),      # texte
    "tv016_departement_code": pa.string(),            # texte : "2A", "2B", "01"
    "latitude": pa.float64(),
    "longitude": pa.float64(),
    "classe_consommation_energie": pa.string(),
    "consommation_energie": pa.float32(),
    "classe_estimation_ges": pa.string(),
    "estimation_ges": pa.float32(),
    "tr002_type_batiment_description": pa.string(),
    "annee_construction": pa.float32(),
    "surface_thermique_lot": pa.float32(),
    "nom_methode_dpe": pa.string(),
    "version_methode_dpe": pa.string(),
    "tr001_modele_dpe_type_libelle": pa.string(),
}

COLUMNS = list(TYPES)

# Schéma Arrow commun à tous les fichiers : garantit que les fichiers intermédiaires
# et le fichier final utilisent les mêmes colonnes et les mêmes types, même si
# certaines colonnes sont vides.
SCHEMA = pa.schema(TYPES.items())


# --- Session HTTP avec reprise automatique ----------------------------------
"""
Crée une session HTTP réutilisable avec des tentatives automatiques
en cas d'erreurs temporaires du serveur.
"""


def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=8,  # jusqu'à 8 tentatives
        backoff_factor=2,   # délai progressif entre les tentatives
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


# --- Conversion de lignes JSON en table Arrow -------------------------------
"""
Convertit une liste de lignes JSON renvoyées par l'API en table Arrow.
Crée un DataFrame pandas et sélectionne les colonnes attendues.
Convertit chaque colonne vers un type compatible avec TYPES.
Renvoie une table Arrow utilisant le schéma commun.
"""


def rows_to_table(rows: list[dict]) -> pa.Table:
    df = pd.DataFrame(rows).reindex(columns=COLUMNS)   # colonne absente -> vide
    for col, typ in TYPES.items():
        # errors="coerce" transforme les valeurs non convertibles en NaN au lieu de
        # provoquer une erreur.
        if pa.types.is_floating(typ):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif pa.types.is_date(typ):
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
        else:
            df[col] = df[col].astype("string")
    return pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)


# --- Téléchargement ---------------------------------------------------------
"""
Télécharge toutes les pages de l'API, les convertit en Parquet et
sauvegarde régulièrement l'état d'avancement pour permettre la reprise.
"""


def download() -> None:
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    # Reprise ou initialisation du téléchargement
    part_idx, total, received = 0, None, 0
    if CHECKPOINT.exists():
        state = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        if state["next_url"] is None:
            print("Téléchargement déjà terminé.")
            return
        url, total = state["next_url"], state["total"]
        part_idx, received = state["part"], state["received"]
        print(f"Reprise au fichier {part_idx} ({received} lignes déjà enregistrées).")
    else:
        # SORT_FIELD : API ADEME trie les données selon le champ numero_dpe id unique
        params = {"size": API_PAGE_SIZE, "select": ",".join(COLUMNS), "sort": SORT_FIELD}
        url = f"{URL}?{urlencode(params)}"

    buffer: list[dict] = []
    # Barre de progression
    pbar = tqdm(unit="lignes", total=total, initial=received)

    # Téléchargement page par page : requête GET
    while url:
        resp = session.get(url, timeout=(15, 120))
        resp.raise_for_status()
        payload = resp.json()

        if total is None:
            total = payload.get("total")
            pbar.total = total

        results = payload.get("results", [])
        buffer.extend(results)
        pbar.update(len(results))
        url = payload.get("next")

        # Écriture d'un fichier quand le tampon est plein, ou à la dernière page
        if len(buffer) >= ROWS_PER_FILE or url is None:
            if buffer:
                part = PARTS_DIR / f"part_{part_idx:05d}.parquet"
                tmp = part.with_suffix(".tmp")
                # Convertit les données et les écrit au format Parquet compressé.
                pq.write_table(rows_to_table(buffer), tmp, compression="snappy")
                tmp.replace(part)          # un .parquet n'existe jamais à l'état partiel
                received += len(buffer)
                part_idx += 1
                buffer = []
            # Sauvegarde l'état courant après l'écriture du fichier.
            # Si le programme s'arrête ensuite, il pourra reprendre à l'URL
            # suivante sans retélécharger les fichiers déjà enregistrés.
            CHECKPOINT.write_text(
                json.dumps({"next_url": url, "total": total,
                            "part": part_idx, "received": received}),
                encoding="utf-8",
            )
    pbar.close()
    print(f"Téléchargement terminé : {received} lignes dans {part_idx} fichiers.")


# --- Fusion en un seul fichier ----------------------------------------------
"""
Fusionne tous les fichiers intermédiaires en un seul fichier Parquet.
Le traitement se fait fichier par fichier afin d'éviter de charger
l'ensemble des données en mémoire.
"""


def merge_parts() -> None:
    # Recherche tous les fichiers part_XXXXX.parquet et les trie par nom.
    parts = sorted(PARTS_DIR.glob("part_*.parquet"))
    if not parts:
        print("Aucun fichier à fusionner.")
        return
    tmp = FINAL_PATH.with_suffix(".tmp")
    # ParquetWriter permet d'écrire plusieurs tables successives dans un seul fichier Parquet.
    with pq.ParquetWriter(tmp, SCHEMA, compression="snappy") as writer:
        for p in tqdm(parts, desc="Fusion", unit="fichier"):
            writer.write_table(pq.read_table(p, schema=SCHEMA))
    tmp.replace(FINAL_PATH)

    # Vérification du nombre de lignes
    n_final = pq.ParquetFile(FINAL_PATH).metadata.num_rows
    n_parts = sum(pq.ParquetFile(p).metadata.num_rows for p in parts)
    if n_final != n_parts:
        raise RuntimeError(f"Incohérence : {n_final} lignes (final) vs {n_parts} (fichiers).")
    print(f"Fichier final -> {FINAL_PATH} ({n_final} lignes)")


# ----- Exécution du script --------------------------------------------
if __name__ == "__main__":
    download()
    merge_parts()


# ---- Exportation du fichier sur S3 -----------------------

fs = s3fs.S3FileSystem(
    client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
)

MY_BUCKET = "mboutbal"
FILE_PATH_OUT_S3 = f"{MY_BUCKET}/diffusion/dpe.parquet"

with open(FINAL_PATH, "rb") as file_in:
    with fs.open(FILE_PATH_OUT_S3, "wb") as file_out:
        shutil.copyfileobj(file_in, file_out)  # copie le contenu en évitant d'ouvrir en mémoire

print(f"Fichier exporté vers s3://{FILE_PATH_OUT_S3}")
