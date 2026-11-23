"""
OBJECTIF
    Télécharger le jeu de données ADEME « DPE Logements (avant juillet 2021) »
    et l'enregistrer au format parquet.

SOURCE
    API de data.ademe.fr (aucune clé nécessaire).

UTILISATION
    Configurer le paramètre S3_BUCKET en renseignant le nom de son bucket S3 pour enregistrer
    une copie des données (line 136)
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

# %% 1. Explorer le dictionnaire ADEME
URL = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe-france"


def lire_api(url, params=None):
    """Requête GET à l'ADEME et lecture de la réponse JSON."""
    reponse = requests.get(url, params=params, timeout=120)
    reponse.raise_for_status()  # signale une erreur HTTP
    return reponse.json()


# Extraction du schéma des variables
dictionnaire_var = pd.DataFrame(lire_api(URL)["schema"])
print(dictionnaire_var[["key", "title", "description", "type", "format"]])


# %% 2. Choisir les colonnes et leurs types
# Création d'un dictionnaire des variables choisies avec leur type souhaité
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

# Liste du nom des variables
colonnes = list(types_colonnes)


# %% 3. Télécharger, vérifier et enregistrer le Parquet

# Paramètres pour la requête API d'extraction des données
params = {
    "select": ",".join(colonnes),  # Colonnes choisies
    "sort": "numero_dpe",          # Trie des lignes par notre id unique
    "size": 10_000,                # Jusqu'à 10 000 lignes par requête
}

# "page_dpe" : var qui contient la réponse de l'API pour la page en cours de traitement
page_dpe = lire_api(URL + "/lines", params)  # Récupération de la 1ère page (initialisation)
nombre_lignes_attendu = page_dpe["total"]    # Récupération du nb d'obs à extraire

# Préparation de la sortie du fichier parquet
schema = pa.schema(list(types_colonnes.items()))

# Création du chemin de sauvegarde dans le répertoire de travail
fichier = Path("dpe_ademe/dpe.parquet")
fichier.parent.mkdir(exist_ok=True)
temporaire = fichier.with_suffix(".tmp")

# Ajout d'une barre de progression dans l'extraction (/nb total attendu)
with (
    pq.ParquetWriter(temporaire, schema) as parquet,
    tqdm(
        total=nombre_lignes_attendu,
        desc="Téléchargement ADEME",
        unit=" lignes",
    ) as barre_progression,
):
    while True:
        donnees = pd.DataFrame(page_dpe["results"], columns=colonnes)
        donnees["date_etablissement_dpe"] = pd.to_datetime(
            donnees["date_etablissement_dpe"], format="%Y-%m-%d"
        ).dt.date  # Conversion de la date avant application du schéma
        table = pa.Table.from_pandas(donnees, schema=schema, preserve_index=False)
        parquet.write_table(table)  # écriture du parquet avec les types choisis
        barre_progression.update(table.num_rows)  # avancement barre de progression
        lien_suivant = page_dpe.get("next")  # chargement de la page suivante
        if not lien_suivant:
            break
        page_dpe = lire_api(lien_suivant)

print("Vérification du nombre de lignes et des identifiants…")

# DuckDB compte les lignes et les numéros de DPE distincts et non manquants.
nombre_lignes, nombre_ids = duckdb.execute(
    "SELECT COUNT(*), COUNT(DISTINCT numero_dpe) FROM read_parquet(?)",
    [str(temporaire)],
).fetchone()

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


# %% 4. Enregistrer le Parquet dans le bucket S3 si extraction réussie

S3_BUCKET = "mboutbal"  # Nom du bucket à paramétrer

s3 = s3fs.S3FileSystem(
    client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
)

s3.put(str(fichier), f"{S3_BUCKET}/diffusion/dpe_date_22_11.parquet")
print("Fichier copié dans le bucket.")
