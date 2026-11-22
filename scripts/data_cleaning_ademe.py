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

import pandas as pd

data = con.execute(f"""
    SELECT *
    FROM read_parquet('{FILE_PATH_S3}')
""").df()

# Analyse
row, col = data.shape


print((row, col))
'''
print('\n\n\n')
print(data.dtypes)
print('\n\n\n')
print(data.isna().sum()/row)
print('\n\n\n')
print(data.duplicated().sum())
print('\n\n\n')
print(data['classe_consommation_energie'].value_counts())
print('\n\n\n')
print(data['classe_estimation_ges'].value_counts())
print('\n\n\n')

classes = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
print(data[(~data['classe_estimation_ges'].isin(classes)) & (~data['classe_consommation_energie'].isin(classes))])

print(data['tr002_type_batiment_description'].unique())
print(data['tr002_type_batiment_description'].value_counts())
'''
#print(data['annee_construction'].between(1000, 2021).sum())


# Sélections des données d'intérêt
'''
df = data.dropna()
classes = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
df = df[(df['classe_estimation_ges'].isin(classes)) & (df['classe_consommation_energie'].isin(classes))]
df = df[~(df['tr002_type_batiment_description'] == 'Bâtiment collectif à usage principal d\'habitation')]
df = df[df['annee_construction'].between(1700, 2021)]
df['annee_construction'] = pd.to_datetime(df['annee_construction'].astype(int).astype(str), format ='%Y')
print(df.shape)
'''
'''
print(df.dtypes)@@@@@@@
print(df['annee_construction'].unique())
'''

# Carte 
import folium
import geopandas as gpd

#Le code pour faire une carte
#est le même pour les classes estimation_ges et les classes consommation_energie
#on utilise une même fonction pour alléger le code 

#nettoyage des données 
df = data.copy()
df = df[~(df['tr002_type_batiment_description'] == 'Bâtiment collectif à usage principal d\'habitation')]

# On retire les classes aberrantes comme I, N, etc. 
classes = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
df = df[(df['classe_estimation_ges'].isin(classes)) & (df['classe_consommation_energie'].isin(classes))]

# On ne garde que les bâtiments construits entre 1700 et 2021
# pour retirer les bâtiments dont la date de construction est aberrante 
df = df[df['annee_construction'].between(1700, 2021)]
df['annee_construction'] = pd.to_datetime(df['annee_construction'].astype(int).astype(str), format ='%Y')

# On calcule le taux de chaque classe par département 
dpe_dep = (
    df
    .groupby('tv016_departement_code')
    ['classe_estimation_ges']
    .value_counts(normalize = True)
    .rename('proportion')
    .reset_index()
)

# pour merge ensuite, on renomme la colonne avec le numéro des départements 
dpe_dep = dpe_dep.rename(columns = {'tv016_departement_code' : 'Code_Offici_dep'})

taux_passoires = (
    dpe_dep
    [dpe_dep['classe_estimation_ges'].isin(['F', 'G'])]
    .groupby('Code_Offici_dep')
    .sum()
    .reset_index()
)

# Quand la souris survole un département, une étiquette s'affiche 
# avec le taux de chaque classe dans le département 
# Ici, on crée la légende 
dpe_dep['Legende'] = ['{} : {} '.format(row.classe_estimation_ges, round(row.proportion, 2)) for row in dpe_dep.itertuples()]
dpe_dep = (
    dpe_dep
    .sort_values(by = ['Code_Offici_dep', 'Legende'], ascending=[True, True])
    .groupby('Code_Offici_dep')
    ['Legende']
    .sum()
    .rename('Legende_agregee')
    .reset_index()
)


# Fichier avec les contours des départements 
path = '/home/onyxia/Python-pour-la-data-science-ENSAE/georef-france-departement/georef-france-departement-millesime.shp'
departements_gdf = gpd.read_file(path, encoding = 'utf-8')
# Certaines colonnes ont le même nom dans departements_gdf donc on les renomme
departements_gdf.columns = ['Annee', 'Code_Offici_dep', 'Nom_Officie_dep', 'Code_Offici_reg', 'Code_Offici_cour_dep', 'Nom_Officie_dep_maj', 'Nom_Officie_dep_min', 'Nom_Officie_reg', 'Code_Iso_31', 'Type', 'Statut_Depa', 'SIREN', 'Est_une_CTU', 'geometry']

# On ne garde que les départements de France métropolitaine 
departements_gdf = departements_gdf[departements_gdf['Code_Iso_31'] == 'FXX']

# On met le numéro du département sous la bonne forme pour merge ensuite 
# On passe de '['01']' à '01'
departements_gdf['Code_Offici_dep'] = departements_gdf['Code_Offici_dep'].apply(lambda x : x[2:4])

departements_gdf = departements_gdf.merge(dpe_dep, how = 'left', on = 'Code_Offici_dep')
#Conversion en GeoJSON
departements_json = departements_gdf.to_json()

# Création de la carte 
carte = folium.Map(location = (47,1), zoom_start = 6)

chloropeth = folium.Choropleth(
    geo_data = departements_json,
    data = taux_passoires,
    columns = ['Code_Offici_dep', 'proportion'],
    key_on = 'feature.properties.Code_Offici_dep',
    fill_color = 'YlOrRd',
    fill_opacity = 0.5,
    line_opacity = 1,
    legend_name = 'Taux de passoires dans le département'
)

chloropeth.geojson.add_child(folium.features.GeoJsonTooltip(
    fields=['Legende_agregee'],
    aliases=['Proportion :'], 
    label = False)
    )
chloropeth.add_to(carte)
carte.save('carte.html')