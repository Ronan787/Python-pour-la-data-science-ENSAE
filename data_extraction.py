from pathlib import Path
import duckdb

sql_file = Path("dpe_logement_202103.sql")
parquet_file = Path("dpe_logement_202103.parquet")

# 1. Connexion en mémoire
con = duckdb.connect()

# 2. Reconstitution et requête sur la table intermédiaire
# DuckDB peut lire le fichier SQL directement via sa fonction d'import
print("1/2. Création de la table intermédiaire et conversion...")

# Requête SQL directe
query = f"""
    COPY (
        SELECT * 
        FROM read_csv_auto('{sql_file}', ignore_errors=true)
    ) TO '{parquet_file}' (FORMAT PARQUET, COMPRESSION 'ZSTD');
"""

con.execute(query)

print("2/2. Terminé !")
print(f"Fichier Parquet généré : {parquet_file}")