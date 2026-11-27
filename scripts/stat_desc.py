
"""
OBJECTIF
    Générer cartes et graphiques en vue d'obtenir des statistiques descriptives à l'échelle nationale.

GRAPHIQUES ET CARTES POUVANT ETRE GENERES
    Carte de la France métropolitaine avec le taux de passoire et la proportion d'étiquette par département 
    Barplot représentant la proportion d'étiquettes
    Boxplot des émissions/consommations en fonction de l'étiquette 
    Heatmap en fonction de l'étiquette et de l'année de construction
    Barplot représentant la proportion d'étiquettes en fonction du type de bâtiment
    Tableau présentant des statistiques descriptives sur les émissions/consommations 

REMARQUES
    Tous les graphiques n'ont pas servi pour le rapport final.
    Des pistes ont été explorées, toutes n'ont pas été conservées.
    Une fonction correspond à un graphique ou à une carte. 
    Les graphiques, ainsi que les cartes, ont tous été générés en deux exemplaires : 
    l'un pour les classes ges, l'autre pour les classes énergie. 

UTILISATION
    pip install -r requirements.txt
    python scripts/stat_desc.py
"""

# Librairie nécessaires par la suite 
import folium
import geopandas as gpd
import s3fs
import shutil
import numpy as np
import matplotlib.pyplot as plt
import duckdb
import pandas as pd
import mimetypes

# Fonction upload_to_s3 présente dans data_extraction_ademe.py avec quelques modifications 
# notamment l'ajout d'un argument file permet d'utiliser cette fonction par la suite 
# pour toute sorte de graphiques à enregistrer dans l'espace de stockage du SSPCloud.
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

# Récupération des données
# nettoyées dans data_cleaning_ademe.py 
# et enregistrées dans l'espace de stockage du SSPCloud
dpe = duckdb.sql("""
    SELECT *
    FROM read_parquet('s3://mboutbal/diffusion/dpe_clean.parquet')
""").df()


## Carte de la France métropolitaine avec le taux de passoire et la proportion d'étiquette par département
# Fichier avec les contours des départements enregistré dans l'espace de stockage du SSPCloud
BUCKET_R = "ron"
path = f"s3://{BUCKET_R}/diffusion/georef-france-departement/georef-france-departement-millesime.shp"

# Lignes de code communes aux classes ges et energie
# C'est pour ça qu'elle sont en dehors de la fonction carte 
# pour ne pas les répéter 
departements_gdf = gpd.read_file(path, encoding = 'utf-8')

# Certaines colonnes ont le même nom dans departements_gdf donc on les renomme
departements_gdf.columns = ['Annee', 'Code_Offici_dep', 'Nom_Officie_dep', 'Code_Offici_reg', 'Code_Offici_cour_dep', 'Nom_Officie_dep_maj', 'Nom_Officie_dep_min', 'Nom_Officie_reg', 'Code_Iso_31', 'Type', 'Statut_Depa', 'SIREN', 'Est_une_CTU', 'geometry']

# On ne garde que les départements de France métropolitaine 
departements_gdf = departements_gdf[departements_gdf['Code_Iso_31'] == 'FXX']

# On met le numéro du département sous la bonne forme pour merge ensuite 
# On passe de '['01']' à '01'
departements_gdf['Code_Offici_dep'] = departements_gdf['Code_Offici_dep'].apply(lambda x : x[2:4])

# Fonction qui génére la carte 
def carte(colonne = 'classe_ges', departements_gdf = departements_gdf):
    # On calcule la proportion de chaque classe par département 
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

    # On calcule ensuite le taux de passoires par département pour colorer les départements en fonction de celui-ci
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
    # Pour chaque classe lui associe sa proportion
    # Avec une disjonction de cas en fonction de la classe : ges ou energie
    if colonne == 'classe_ges':
        dpe_dep['Legende'] = ['{} : {} '.format(row.classe_ges, round(row.proportion, 2)) for row in dpe_dep.itertuples()]
    else :
        dpe_dep['Legende'] = ['{} : {} '.format(row.classe_energie, round(row.proportion, 2)) for row in dpe_dep.itertuples()]

    # On agrège les légendes pour avoir la proportion de chaque classe 
    dpe_dep = (
        dpe_dep
        .sort_values(by = ['Code_Offici_dep', 'Legende'], ascending=[True, True])
        .groupby('Code_Offici_dep')
        ['Legende']
        .sum()
        .rename('Legende_agregee')
        .reset_index()
    )

    # On merge les contours des départements et les proportions des classes pour chaque département
    departements_gdf = departements_gdf.merge(dpe_dep, how = 'left', on = 'Code_Offici_dep')
    #Conversion en GeoJSON
    departements_json = departements_gdf.to_json()

    # Création de la carte 
    carte = folium.Map(location = (47,1), zoom_start = 6)

    # On ajoute la couleur
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
    # On ajoute la légende quand la souris survole le département
    chloropeth.geojson.add_child(folium.features.GeoJsonTooltip(
        fields=['Legende_agregee'],
        aliases=['Proportion :'], 
        label = False)
        )
    chloropeth.add_to(carte)

    # On enregistre la carte 
    # dans l'espace de stockage du SSPCloud
    carte.save(f'carte_{colonne}.html')
    upload_to_s3(BUCKET_R, f'carte_{colonne}.html')


#Barplot représentant la proportion d'étiquettes
def barplot(colonne = 'classe_ges'):
    # On réinitialise la figure 
    plt.clf()
    # On calcule la proportion de chaque étiquette 
    (
        dpe[colonne]
        .value_counts(normalize = True)
        .rename('proportion')
        .reset_index()
        .sort_values(by = colonne)
        .plot.bar(x = colonne, y = 'proportion', legend = False, color='#6baed6') # et on crée un barplot à partir de celui-ci 
    )
    #légendes
    # Dans un souci de généralisation, on utilise l'argument colonne pour la légende de l'axe des abscisses et le titre 
    col_bis = f'{colonne}'.replace("_", " ")
    plt.title(f'Proportion de chaque {col_bis}')
    plt.xlabel(col_bis.capitalize())
    plt.ylabel('Proportion')

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"bar_plot_{colonne}.png")
    upload_to_s3(BUCKET_R, f"bar_plot_{colonne}.png")
    plt.clf()

#Boxplot des émissions/consommations en fonction de l'étiquette
def boxplot(colonne = 'emissions_ges', by = 'classe_ges'):
    plt.clf()
    # On crée le boxplot 
    dpe.boxplot(column=[colonne], by= by)
    # La suite sert à mettre en forme la légende à partir des arguments colonne et by 
    # dans un souci de généralisation
    plt.suptitle('')
    col_bis = f'{colonne}'.replace("_", " ")
    by_bis = f'{by}'.replace("_", " ")
    plt.title('Boxplots de {} par {}'.format(col_bis, by_bis))
    plt.xlabel(by_bis.capitalize())
    if by == 'classe_ges':
        plt.ylabel(f'{col_bis.capitalize()} en kgéqCO2/m².an')
    else :
        plt.ylabel(f'{col_bis.capitalize()} en kWhEP/m².an')
    plt.legend()

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"boxplot_{by}.png")
    upload_to_s3(BUCKET_R, f"boxplot_{by}.png")
    plt.clf()

#Boxplot des émissions/consommations en fonction de l'étiquette
def boxplot_avec_couleurs(colonne = 'emissions_ges', by = 'classe_ges'):
    # Même chose que la fonction précédente 
    # avec en plus des bandes colorées qui représentent les classes théoriques 
    plt.clf()
    ax = dpe.boxplot(column=[colonne], by=by)
    # On supprime la grille en arrière plan 
    ax.grid(False)
    ax.xaxis.grid(False)
    ax.yaxis.grid(False)

    # On explicite les zones en fonction de si l'étiquette correspond à une classe ges ou à une classe energie 
    ymin, ymax = plt.ylim()
    if by == 'classe_ges': 
        plt.axhspan(ymin, 5, color='#7f7f7f', alpha=0.2, label='classe A') # Gris
        plt.axhspan(6, 10, color='#31a354', alpha=0.2, label='classe B') # Vert
        plt.axhspan(11, 20, color='#6baed6', alpha=0.2, label='classe C') # Bleu
        plt.axhspan(21, 35, color='#8c6bb1', alpha=0.2, label='classe D') # Violet
        plt.axhspan(36, 55, color='#e6ab02', alpha=0.2, label='classe E') # Jaune moutarde
        plt.axhspan(56, 80, color='#e6550d', alpha=0.2, label='classe F') # Orange
        plt.axhspan(81, ymax, color='#dd6a8c', alpha=0.2, label='classe G') # Rose
    else : 
        plt.axhspan(ymin, 50, color='#7f7f7f', alpha=0.2, label='classe A') # Gris
        plt.axhspan(51, 90, color='#31a354', alpha=0.2, label='classe B') # Vert
        plt.axhspan(91, 150, color='#6baed6', alpha=0.2, label='classe C') # Bleu
        plt.axhspan(151, 230, color='#8c6bb1', alpha=0.2, label='classe D') # Violet
        plt.axhspan(231, 330, color='#e6ab02', alpha=0.2, label='classe E') # Jaune moutarde
        plt.axhspan(331, 450, color='#e6550d', alpha=0.2, label='classe F') # Orange
        plt.axhspan(451, ymax, color='#dd6a8c', alpha=0.2, label='classe G') # Rose

    plt.suptitle('')
    col_bis = f'{colonne}'.replace("_", " ")
    by_bis = f'{by}'.replace("_", " ")
    plt.title('Boxplots de {} par {}'.format(col_bis, by_bis))
    plt.xlabel(by_bis.capitalize())
    if by == 'classe_ges':
        plt.ylabel(f'{col_bis.capitalize()} en kgéqCO2/m².an')
    else :
        plt.ylabel(f'{col_bis.capitalize()} en kWhEP/m².an')
    plt.legend()

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"boxplot_avec_couleur_{by}.png")
    upload_to_s3(BUCKET_R, f"boxplot_avec_couleur_{by}.png")
    plt.clf()

def par_annee_construction(colonne = 'classe_ges'):
    plt.clf()
    # On définit les extrémités des cases 
    # Une case correspond à une période de 20 ans et à une étiquette 
    extremites = [1901 + i*20 for i in range(0, 7)]
    labels = ['Entre {} et {}'.format(extremites[i], extremites[i+1]) for i in range(0, 6)]

    # Arbitrairement, on ne garde que les bâtiments contruits après 1900
    dpe_reduit = dpe[dpe['annee_construction'].between(1901, 2021)]
    # On crée les tranches d'années 
    dpe_reduit["tranche_annee"] = pd.cut(dpe_reduit['annee_construction'], bins=extremites, labels=labels)

    #nombre de DPE en fonction de l'année 
    # On crée un tableau 
    tableau = pd.crosstab(dpe_reduit["tranche_annee"], dpe_reduit[colonne])
    # Puis une heatmap
    plt.imshow(tableau, cmap="YlOrRd", aspect="auto")

    # on ajoute des catégories sur les axes
    plt.xticks(
        range(len(tableau.columns)),
        tableau.columns
    )

    plt.yticks(
        range(len(tableau.index)),
        tableau.index
    )

    # on ajouter une barre de couleur
    plt.colorbar(label="Nombre de DPE")
    plt.title('Nombre de DPE par année de construction')
    plt.xlabel(f'{colonne}'.capitalize().replace('_', ' '))
    plt.ylabel('Année de construction')
    plt.savefig(f"nb_dpe_par_annee_{colonne}.png")

    # On enregistre dans l'espace de stockage du SSPCloud
    upload_to_s3(BUCKET_R, f"nb_dpe_par_annee_{colonne}.png")
    plt.clf()

def par_annee_construction_zoom(colonne = 'classe_ges'):
    # Même chose que précédemment mais cette fois on ne crée pas de tranches
    # Une case correspond à une année et à une étiquette
    # On ne s'intéresse qu'aux bâtiments construits entre 2000 et 2021
    plt.clf()
    dpe_reduit = dpe[dpe['annee_construction'].between(2000, 2021)]

    #nombre de DPE en fonction de l'année 
    tableau = pd.crosstab(dpe_reduit['annee_construction'], dpe_reduit[colonne])
    plt.imshow(tableau, cmap="YlOrRd", aspect="auto")

    # on ajoute des catégories sur les axes
    plt.xticks(
        range(len(tableau.columns)),
        tableau.columns
    )

    plt.yticks(
        range(len(tableau.index)),
        tableau.index
    )

    # on ajoute une barre de couleur
    plt.colorbar(label="Nombre de DPE")
    plt.title('Nombre de DPE par année de construction')
    plt.xlabel(f'{colonne}'.capitalize().replace('_', ' '))
    plt.ylabel('Année de construction')

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"nb_dpe_par_annee_zoom_{colonne}.png")
    upload_to_s3(BUCKET_R, f"nb_dpe_par_annee_zoom_{colonne}.png")
    plt.clf()

def par_annee_construction2 (colonne = 'classe_ges'):
    # même chose que les deux fonctions précédentes mais côte à côte 
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # Premier graphique : tranches de 20 ans
    extremites = [1901 + i*20 for i in range(0, 7)]
    labels = [
        'Entre {} et {}'.format(extremites[i], extremites[i+1])
        for i in range(0, 6)
    ]

    dpe_reduit = dpe[
        dpe['annee_construction'].between(1901, 2021)
    ].copy()

    dpe_reduit["tranche_annee"] = pd.cut(
        dpe_reduit['annee_construction'],
        bins=extremites,
        labels=labels
    )

    tableau = pd.crosstab(
        dpe_reduit["tranche_annee"],
        dpe_reduit[colonne]
    )

    im1 = axes[0].imshow(
        tableau,
        cmap="YlOrRd",
        aspect="auto"
    )

    axes[0].set_xticks(range(len(tableau.columns)))
    axes[0].set_xticklabels(tableau.columns)

    axes[0].set_yticks(range(len(tableau.index)))
    axes[0].set_yticklabels(tableau.index)

    axes[0].set_title('Nombre de DPE par tranche de construction')
    axes[0].set_xlabel(f'{colonne}'.capitalize().replace('_', ' '))
    axes[0].set_ylabel('Année de construction')

    fig.colorbar(im1, ax=axes[0], label="Nombre de DPE")

    # Deuxième graphique : année par année
    dpe_reduit = dpe[
        dpe['annee_construction'].between(2000, 2021)
    ]

    tableau = pd.crosstab(
        dpe_reduit['annee_construction'],
        dpe_reduit[colonne]
    )

    im2 = axes[1].imshow(
        tableau,
        cmap="YlOrRd",
        aspect="auto"
    )

    axes[1].set_xticks(range(len(tableau.columns)))
    axes[1].set_xticklabels(tableau.columns)

    axes[1].set_yticks(range(len(tableau.index)))
    axes[1].set_yticklabels(tableau.index)

    axes[1].set_title('Nombre de DPE par année de construction')
    axes[1].set_xlabel(colonne)
    axes[1].set_ylabel('Année de construction')

    fig.colorbar(im2, ax=axes[1], label="Nombre de DPE")

    plt.tight_layout()

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"nb_dpe_par_annee2_{colonne}.png")
    upload_to_s3(BUCKET_R, f"nb_dpe_par_annee2_{colonne}.png")

    plt.close(fig)

def barplot_type(colonne = 'classe_ges'):
    # Même chose que la fonction barplot précédente 
    # On rajoute les méthodes .unstack() et .transpose()
    # pour obtenir le graphique voulu
    plt.clf()
    (
        dpe
        .sort_values(by = colonne)
        .groupby('type_batiment')
        [colonne]
        .value_counts(normalize = True)
        .rename('proportion')
        .unstack()
        .transpose()
        .plot.bar(rot=0, color = ['#7f7f7f', '#31a354', '#6baed6'])
        
    )
    #légendes
    col_bis = f'{colonne}'.replace('_', ' ')
    plt.title(f'Proportion de chaque {col_bis} par type de bâtiment')
    plt.ylabel('Proportion')
    plt.xlabel(col_bis.capitalize())

    carres, labels = plt.gca().get_legend_handles_labels()
    labels = ['Appartement' if label == 'Logement' else label for label in labels]
    plt.legend(carres, labels)

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"bar_plot_type_{colonne}.png")
    upload_to_s3(BUCKET_R, f"bar_plot_type_{colonne}.png")
    plt.clf()

def tableau(colonne):
    # On utilise la méthode .describe()
    # pour obtenir les statistiques descriptives voulues
    if colonne == 'classe_ges' :
        plt.clf()
        stat_dpe = (
            dpe
            .groupby('classe_ges')
            ['emissions_ges']
            .describe()
        )
    else : 
        plt.clf()
        stat_dpe = (
            dpe
            .groupby('classe_energie')
            ['conso_energie']
            .describe()
        )

    fig, ax = plt.subplots(figsize=(10, 4))

    ax.axis('off')  # pour masquer le repère cartésien
    # On crée un tableau à partir du data frame crée précédemment avec les statistiques descriptives 
    table = ax.table(
        cellText=stat_dpe.values.round(2),
        colLabels=stat_dpe.columns,
        rowLabels=stat_dpe.index,
        loc="center",
        cellLoc="center",
        bbox=[0, 0, 1, 1]  # Occupe toute la surface disponible
    )

    table.auto_set_font_size(False)
    table.set_fontsize(11)

    plt.tight_layout()

    # On enregistre dans l'espace de stockage du SSPCloud
    plt.savefig(f"tableau_{colonne}.png")
    upload_to_s3(BUCKET_R, f"tableau_{colonne}.png")
    plt.clf()

if __name__ == "__main__":
    # On génére les cartes qui nous intéressent pour le rapport
    carte('classe_ges')
    carte('classe_energie')
    barplot('classe_ges')
    barplot('classe_energie')
    #boxplot('emissions_ges', 'classe_ges')
    boxplot_avec_couleurs('emissions_ges', 'classe_ges')
    #boxplot('conso_energie', 'classe_energie')
    boxplot_avec_couleurs('conso_energie', 'classe_energie')
    par_annee_construction2('classe_ges')
    par_annee_construction2('classe_energie')
    #par_annee_construction_zoom('classe_ges')
    #par_annee_construction_zoom('classe_energie')
    barplot_type('classe_ges')
    barplot_type('classe_energie')
    #tableau('classe_ges')
    #tableau('classe_energie')

