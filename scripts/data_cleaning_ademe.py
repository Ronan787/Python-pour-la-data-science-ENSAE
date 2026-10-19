"""
OBJECTIF
    1. Importer les données extraites par le script scripts/data_extraction_ademe.py depuis S3
    2. Nettoyer et formater les données
"""


import duckdb

# %% --- Importation des données depuis S3 ------------------

MY_BUCKET = "mboutbal"
FILE_PATH_S3 = f"s3://{MY_BUCKET}/diffusion/dpe.parquet"

con = duckdb.connect()


# %% --- Vérification du dataset en lazy evaluation ----------------------
nb_lines = con.execute(f"""
    SELECT COUNT(DISTINCT numero_dpe)
    FROM read_parquet('{FILE_PATH_S3}')
""").fetchone()

print("Nombre de numero_dpe uniques :", nb_lines[0])

first_lines = con.execute(f"""
    SELECT *
    FROM read_parquet('{FILE_PATH_S3}')
    LIMIT 5
""").df()

print(first_lines)

# %% --- Analyse des variables préalable au nettoyage ---------------------
