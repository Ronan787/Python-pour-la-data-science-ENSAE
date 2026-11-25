

# Carte 
import folium
import geopandas as gpd
import s3fs
import shutil
import numpy as np
import matplotlib.pyplot as plt
import duckdb
import pandas as pd
import mimetypes
#from data_extraction_ademe.py import upload_to_s3

def upload_to_s3(s3_bucket, file):
    """
    Enregistre le fichier dans le dossier diffusion du bucket défini.
    """
    if not s3_bucket or s3_bucket == "nom_du_bucket":
        print("Export S3 ignoré : renseignez le nom du bucket.")
        return

    fs = s3fs.S3FileSystem(
        client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
    )

    file_path_out_s3 = "{}/diffusion/{}".format(s3_bucket, file)

    content_type, _ = mimetypes.guess_type(file)

    if content_type is None:
        content_type = "application/octet-stream"

    with open(file, "rb") as file_in:
        with fs.open(file_path_out_s3, "wb",
            ContentType=content_type,
            ContentDisposition="inline") as file_out:
            shutil.copyfileobj(file_in, file_out)

    print(f"Fichier exporté vers s3://{file_path_out_s3}")

#Récupération des données 
dpe = duckdb.sql("""
    SELECT *
    FROM read_parquet('s3://mboutbal/diffusion/dpe_clean.parquet')
""").df()
dpe = dpe[dpe['annee_construction'].between(1700, 2021)]


## Carte
# Fichier avec les contours des départements 
BUCKET_R = "ron"
path = f"s3://{BUCKET_R}/diffusion/georef-france-departement/georef-france-departement-millesime.shp"

departements_gdf = gpd.read_file(path, encoding = 'utf-8')
# Certaines colonnes ont le même nom dans departements_gdf donc on les renomme
departements_gdf.columns = ['Annee', 'Code_Offici_dep', 'Nom_Officie_dep', 'Code_Offici_reg', 'Code_Offici_cour_dep', 'Nom_Officie_dep_maj', 'Nom_Officie_dep_min', 'Nom_Officie_reg', 'Code_Iso_31', 'Type', 'Statut_Depa', 'SIREN', 'Est_une_CTU', 'geometry']

# On ne garde que les départements de France métropolitaine 
departements_gdf = departements_gdf[departements_gdf['Code_Iso_31'] == 'FXX']

# On met le numéro du département sous la bonne forme pour merge ensuite 
# On passe de '['01']' à '01'
departements_gdf['Code_Offici_dep'] = departements_gdf['Code_Offici_dep'].apply(lambda x : x[2:4])

def carte(colonne = 'classe_ges', departements_gdf = departements_gdf):
    # On calcule le taux de chaque classe par département 
    dpe_dep = (
        dpe
        .groupby('code_departement')
        [colonne]
        .value_counts(normalize = True)
        .rename('proportion')
        .reset_index()
    )

    # pour merge ensuite, on renomme la colonne avec le numéro des départements 
    dpe_dep = dpe_dep.rename(columns = {'code_departement' : 'Code_Offici_dep'})

    taux_passoires = (
        dpe_dep
        [dpe_dep[colonne].isin(['F', 'G'])]
        .groupby('Code_Offici_dep')
        .sum()
        .reset_index()
    )

    # Quand la souris survole un département, une étiquette s'affiche 
    # avec le taux de chaque classe dans le département 
    # Ici, on crée la légende 
    if colonne == 'classe_ges':
        dpe_dep['Legende'] = ['{} : {} '.format(row.classe_ges, round(row.proportion, 2)) for row in dpe_dep.itertuples()]
    else :
        dpe_dep['Legende'] = ['{} : {} '.format(row.classe_energie, round(row.proportion, 2)) for row in dpe_dep.itertuples()]
    dpe_dep = (
        dpe_dep
        .sort_values(by = ['Code_Offici_dep', 'Legende'], ascending=[True, True])
        .groupby('Code_Offici_dep')
        ['Legende']
        .sum()
        .rename('Legende_agregee')
        .reset_index()
    )

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
    carte.save(f'carte_{colonne}.html')

    upload_to_s3(BUCKET_R, f'carte_{colonne}.html')


# bar plots
def barplot(colonne = 'classe_ges'):
# Tracé de l'histogramme
    plt.clf()
    (
        dpe[colonne]
        .value_counts(normalize = True)
        .rename('proportion')
        .reset_index()
        .sort_values(by = colonne)
        .plot.bar(x = colonne, y = 'proportion')
    )
    #légendes
    plt.title(f'Proportion de chaque {colonne}')
    plt.xlabel(f'{colonne}')
    plt.ylabel('Proportion')
    plt.savefig(f"bar_plot_{colonne}.png")
    upload_to_s3(BUCKET_R, f"bar_plot_{colonne}.png")
    plt.clf()


#nombre de DPE en fonction de l'année 
#tableau = pd.crosstab(dpe['annee_construction'].dt.year, dpe['classe_ges'])
tableau = pd.crosstab(dpe['annee_construction'], dpe['classe_ges'])
print(tableau)
plt.imshow(tableau, cmap="YlOrRd", aspect="auto")

# Ajoute des catégories sur les axes
plt.xticks(
    range(len(tableau.columns)),
    tableau.columns
)

plt.yticks(
    range(len(tableau.index)),
    tableau.index
)

# Ajouter une barre de couleur
plt.colorbar(label="Nombre de DPE")
plt.title('Nombre de DPE par année de construction')
plt.xlabel('Classe GES')
plt.ylabel('Année de construction')
plt.savefig("nb_dpe_par_annee.png")



def boxplot(colonne = 'emissions_ges', by = 'classe_ges'):
    plt.clf()
    dpe.boxplot(column=[colonne], by= by)
    plt.suptitle('')
    plt.title('Boxplots de {} par {}'.format(colonne, by))
    plt.xlabel(f'{by}')
    plt.ylabel(f'{colonne} en kwH ')
    plt.savefig(f"boxplot_{by}.png")
    upload_to_s3(BUCKET_R, f"boxplot_{by}.png")
    plt.clf()

if __name__ == "__main__":
    carte('classe_ges')
    print('carte classe_ges fait')
    carte('classe_energie')
    print('carte classe_energie fait')
    barplot('classe_ges')
    print('barplot classe_ges fait')
    barplot('classe_energie')
    print('barplot classe_energie fait')
    boxplot('emissions_ges', 'classe_ges')
    print('boxplot classe_ges fait')
    boxplot('conso_energie', 'classe_energie')