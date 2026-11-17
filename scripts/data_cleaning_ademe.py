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

df = data.copy()
classes = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
df = df[(df['classe_estimation_ges'].isin(classes)) & (df['classe_consommation_energie'].isin(classes))]
df = df[~(df['tr002_type_batiment_description'] == 'Bâtiment collectif à usage principal d\'habitation')]
df = df[df['annee_construction'].between(1700, 2021)]
df['annee_construction'] = pd.to_datetime(df['annee_construction'].astype(int).astype(str), format ='%Y')

dpe_dep = (
    df
    .groupby('tv016_departement_code')
    ['classe_estimation_ges']
    .value_counts(normalize = True)
    .rename('proportion')
    .reset_index()
    #.reset_index()
)
print(dpe_dep[:5])
dpe_dep['Legende1'] = ['{} : {} \n'.format(row.classe_estimation_ges, round(row.proportion, 2)) for row in dpe_dep.itertuples()]
dpe_dep = (
    dpe_dep
    .sort_values(by = ['tv016_departement_code', 'Legende1'], ascending=[True, True])
    .groupby('tv016_departement_code')
    ['Legende1']
    .sum()
    .rename('Legende')
    .reset_index()
    #.reset_index()
)
dpe_dep = dpe_dep.rename(columns = {'tv016_departement_code' : 'Code_Offici_dep'})
print((dpe_dep['Code_Offici_dep']).dtype)
#print(dpe_dep[:5])
'''
limites_max = [(48.704826,2.156067), (48.71925,2.22383)]
#carte = folium.Map(location = (df['latitude'].mean(), df['longitude'].mean()), zoom_start = 15)
carte = folium.Map(location = (48.710999,2.207608), zoom_start = 12, max_bounds = True)
carte.fit_bounds(limites_max)

lim = (df['latitude'].between(limites_max[0][0], limites_max[1][0])) & (df['longitude'].between(limites_max[0][1], limites_max[1][1]))
for row in df[lim].itertuples() : 
    folium.Marker(
        location = (row.latitude, row.longitude),
        popup = row.classe_consommation_energie,
        icon=folium.Icon(color='blue')
    ).add_to(carte)

carte.save('carte.html')
'''
path = '/home/onyxia/Python-pour-la-data-science-ENSAE/georef-france-departement/georef-france-departement-millesime.shp'
departements_gdf = gpd.read_file(path, encoding = 'utf-8')
#print(departements_gdf.columns)
departements_gdf.columns = ['Annee', 'Code_Offici_dep', 'Nom_Officie_dep', 'Code_Offici_reg', 'Code_Offici_cour_dep', 'Nom_Officie_dep_maj', 'Nom_Officie_dep_min', 'Nom_Officie_reg', 'Code_Iso_31', 'Type', 'Statut_Depa', 'SIREN', 'Est_une_CTU', 'geometry']
#print(departements_gdf.columns)
departements_gdf = departements_gdf[departements_gdf['Code_Iso_31'] == 'FXX']
print(type(departements_gdf['Code_Offici_dep'].iloc[0]))
departements_gdf['Code_Offici_dep'] = departements_gdf['Code_Offici_dep'].apply(lambda x : x[2:4])
#essonne_gdf = departements_gdf[departements_gdf['Nom_Officie_dep'] == 'essonne']
#print(departements_gdf['Nom_Officie_dep'].unique())

#essonne_gdf = essonne_gdf.set_index('Nom_Officie_dep')
#essonne_gdf = essonne_gdf['geometry']
#print(essonne_gdf[['Nom_Officie_dep', 'geometry']])
#Conversion en GeoJSON
#essonne_json = essonne_gdf.to_json()
carte = folium.Map(location = (47,1), zoom_start = 6)
#chloropeth = folium.Choropleth(geo_data = essonne_json)
#chloropeth.add_to(carte)

departements_gdf = departements_gdf.merge(dpe_dep, how = 'left', on = 'Code_Offici_dep')
print(departements_gdf.head())
departements_json = departements_gdf.to_json()

chloropeth = folium.Choropleth(geo_data = departements_json)
chloropeth.geojson.add_child(folium.features.GeoJsonTooltip(
    fields=['Legende'],
    aliases=['Légende :'], 
    label = False)
    )
chloropeth.add_to(carte)

'''
folium.GeoJson(
    essonne_json,
    name='Essonne',
    style_function=lambda feature: {
        'fillColor': 'red',
        'color': 'black',
        'weight': 2,
        'fillOpacity': 0.6
    },
    tooltip=folium.GeoJsonTooltip(
        fields=['Nom_Officie_dep'],
        aliases=['Département :']
    )
).add_to(carte)
'''
carte.save('carte.html')
