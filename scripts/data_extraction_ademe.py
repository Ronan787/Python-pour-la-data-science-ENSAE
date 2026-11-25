"""
OBJECTIF
    Télécharger le jeu de données ADEME « DPE Logements (avant juillet 2021) »
    et l'enregistrer au format parquet.

SOURCE
    API de data.ademe.fr (aucune clé nécessaire).

UTILISATION
    Configurer les paramètre S3_BUCKET en renseignant le nom de son bucket S3 pour enregistrer
    une copie des données et VERSION qui sera le nom du fichier
    pip install -r requirements.txt
    python scripts/data_extraction_ademe.py
"""

from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
import s3fs
from tqdm.auto import tqdm

# PARAMETRES A CONFIGURER
S3_BUCKET = "mboutbal"  # Nom du bucket à paramétrer pour créer une sauvegarde
VERSION = "dpe_2026-11-25"  # Nom du fichier final dans S3

# URL de l'API (source des données)
URL = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe-france"

# Création du chemin de sauvegarde dans le répertoire de travail
fichier = Path("dpe_ademe/dpe.parquet")
fichier.parent.mkdir(exist_ok=True)
temporaire = fichier.with_suffix(".tmp")  # Fichier temporaire avant écriture en parquet


# Fonction pour requêter l'api
def lire_api(url, params=None):
    """Requête GET à l'ADEME et lecture de la réponse JSON."""
    reponse = requests.get(url, params=params, timeout=120)
    reponse.raise_for_status()  # signale une erreur HTTP
    return reponse.json()


# Extraction du schéma des variables pour exploration
dictionnaire_var = pd.DataFrame(lire_api(URL)["schema"])
print(dictionnaire_var[["key", "title", "description", "type", "format"]])

# Choix des colonnes à extraire et de leur type
types_colonnes = {
    "numero_dpe": pa.string(),
    "nom_methode_dpe": pa.string(),
    "version_methode_dpe": pa.string(),
    "date_etablissement_dpe": pa.date32(),  # Date sans heure
    "annee_construction": pa.int64(),
    "tr001_modele_dpe_type_libelle": pa.string(),
    "tr002_type_batiment_description": pa.string(),
    "consommation_energie": pa.float64(),
    "classe_consommation_energie": pa.string(),
    "estimation_ges": pa.float64(),
    "classe_estimation_ges": pa.string(),
    "tv016_departement_code": pa.string(),
    "code_insee_commune_actualise": pa.string(),
    "latitude": pa.float64(),
    "longitude": pa.float64(),
    "geo_adresse": pa.string(),
    "geo_score": pa.float64(),
}

# List récupère uniquement les noms des variables
colonnes = list(types_colonnes)
# pa.schema() transforme les couples (nom, type) en schéma PyArrow
schema = pa.schema(list(types_colonnes.items()))

# Paramètres pour la requête API d'extraction des données
params = {
    "select": ",".join(colonnes),  # Colonnes choisies
    "sort": "numero_dpe",          # Trie des lignes par notre id unique
    "size": 10_000,                # Jusqu'à 10 000 lignes par requête
}

# "page_dpe" : var qui contient la réponse de l'API pour la page en cours de traitement
page_dpe = lire_api(URL + "/lines", params)  # Récupération de la 1ère page (initialisation)
nombre_lignes_attendu = page_dpe["total"]    # Récupération du nb d'obs à extraire

# ------- BLOC D'EXTRACTION PRINCIPAL --------#
with (
    pq.ParquetWriter(temporaire, schema) as parquet,
    tqdm(
        total=nombre_lignes_attendu,  # nb total de ligne à télécharger
        desc="Téléchargement ADEME",
        unit=" lignes",
    ) as barre_progression,  # ajout d'une barre de progression dans l'extraction
):
    while True:
        # results contient toutes les obs à extraire
        donnees = pd.DataFrame(page_dpe["results"], columns=colonnes)

        # Conversion de la date avant application du schéma
        donnees["date_etablissement_dpe"] = pd.to_datetime(
            donnees["date_etablissement_dpe"], format="%Y-%m-%d"
        ).dt.date

        # application du schema (cf. param du début) : même types de var à toutes les pages
        table = pa.Table.from_pandas(donnees, schema=schema, preserve_index=False)

        # écriture du parquet avec les types choisis
        parquet.write_table(table)

        # avancement barre de progression
        barre_progression.update(table.num_rows)

        # chargement de la page suivante ou fin de l'extraction si pas de page suivante
        lien_suivant = page_dpe.get("next")
        if not lien_suivant:
            break
        page_dpe = lire_api(lien_suivant)

print("Vérification du nombre de lignes et des identifiants…")

# ------- Vérification de la cohérence du fichier parquet créé avec DuckDB -----#

# Requête pour avoir le nb de lignes et le nb de numéros de DPE distincts
nombre_lignes, nombre_ids = duckdb.execute(
    "SELECT COUNT(*), COUNT(DISTINCT numero_dpe) FROM read_parquet(?)",
    [str(temporaire)],
).fetchone()  # .fetchone() récupère le résultat de la requête SQL

if nombre_lignes != nombre_lignes_attendu:
    raise ValueError(
        f"{nombre_lignes:,} lignes écrites sur "
        f"{nombre_lignes_attendu:,} attendues."
    )

if nombre_ids != nombre_lignes:
    raise ValueError("Certains numéros de DPE sont en double ou manquants.")

# Le fichier prend son nom final après les deux vérifications
temporaire.replace(fichier)
print(f"{nombre_lignes:,} lignes, chacune avec un numéro de DPE unique.")
print(pq.read_schema(fichier))


# ----- Enregistrement d'une copie dans le bucket S3 --------#
s3 = s3fs.S3FileSystem(
    client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
)
# Enregistrement selon les paramètres du début
s3.put(str(fichier), f"{S3_BUCKET}/diffusion/{VERSION}.parquet")
print("Fichier copié dans le bucket.")
