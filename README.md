# Les écoquartiers à l’épreuve des DPE
## Projet Python — ENSAE
Contributeurs : Maëlle Boutbal - Capucine Bouyer - Ronan Gorin

Ce projet vise à déterminer si les données mettent en évidence de meilleures performances énergétiques dans les écoquartiers, ou si leur exemplarité affichée doit être nuancée.
Les résultats obtenus sont regroupés dans un rapport qui prend la forme d'un site web Quarto Markdown, dont le code se trouve dans le fichier **rapport.qmd**.

## Ouvrir le rapport

Les librairies python nécessaires sont regroupées dans le fichier `requirements.txt` : `pip install -r requirements.txt`
Pour ouvrir le site web, il suffit d'écrire la ligne suivante dans le terminal de VS code après avoir ouvert le projet depuis GitHub : `quarto preview rapport.qmd`. Pour créer un fichier `rapport.html`, utiliser `quarto render rapport.qmd` : il doit alors être conservé avec son dossier `rapport_files/`.

**Si le rapport ne se lance pas** (problème avec Quarto, avec l'accès à S3, etc.), une version déjà générée est fournie en secours : [Consulter le rapport Quarto](https://ronan787.github.io/Python-pour-la-data-science-ENSAE/rapport.html). 

 <!-- **rapport.html**, à ouvrir dans un navigateur. Elle doit rester dans le même dossier que **rapport_files/** (sans lui, la page s'affiche sans mise en forme ni images), et il faut télécharger les deux ou cloner le dépôt, car GitHub n'affiche que le code source du fichier. Les cartes interactives sont chargées depuis le stockage SSPCloud : une connexion internet est nécessaire pour les voir. -->

Nous avons fait en sorte de réduire le temps de lancement du site au maximum : les cartes et les graphiques ont été générés au préalable à partir du code et enregistrés dans l’espace de stockage du SSPCloud, que le rapport se contente d'afficher. Malgré ces précautions, le site met un certain temps à se lancer.

Les cartes générées sont interactives. Il est donc possible de naviguer dessus. Cependant, notre étude se limite à la France métropolitaine. Nul besoin d'explorer d'autres continents. 

## Les scripts

Le code est regroupé dans le dossier **scripts**. Il n'est pas nécessaire de le relancer pour ouvrir le rapport.

**Pour enregistrer les résultats dans son propre bucket** (droit d'écriture nécessaire), il suffit de renseigner son nom dans la variable indiquée *« Bucket à paramétrer »* en haut des scripts concernés. Les fonctions qui appellent des données déjà enregistrées sur un de nos bucket S3 en revanche, n'ont pas besoin de paramétrage. Nous avons sauvegardé les fichiers dans le dossier `diffusion` de chaque bucket afin qu'ils soient accessibles.

- data_extraction_ademe.py : contient le code permettant de récupérer les données DPE depuis l'API de l'Ademe. Les données ainsi récupérées sont stockées dans l’espace de stockage du SSPCloud (2 à 3 heures). Pour l'enregistrement : *Bucket à paramétrer : `S3_BUCKET`.*
- data_cleaning_ademe.py : contient le code permettant de nettoyer et de formater les données DPE extraites, appelées depuis l’espace de stockage du SSPCloud. Pour l'enregistrement : *Bucket à paramétrer : `S3_BUCKET`.*
- stat_desc.py : contient le code générant cartes et graphiques en vue d'obtenir des statistiques descriptives à l'échelle nationale. *Bucket à paramétrer : `BUCKET_R`, qui sert aussi à lire le shapefile des départements : le copier dans le même dossier de son bucket (`diffusion/georef-france-departement/`).*
- data_extraction_ecoquartiers.py : contient le code permettant de récupérer les contours des ÉcoQuartiers et de les charger dans un GeoDataFrame.
- data_cleaning_ecoquartiers.py : contient le code permettant de nettoyer les données des ÉcoQuartiers extraites. 
- croisement_dpe_ecoquartiers.py : contient le code permettant de croiser les DPE (points géographiques) avec les périmètres des ÉcoQuartiers. Le tout étant de savoir quels DPE se trouvent dans quel écoquartier.
- stat_desc_ecoquartiers.py : contient le code permettant de réaliser des statistiques descriptives sur les écoquartiers et de les comparer au reste de la France. 
- structure_logement_ecoquartiers.py : compare la structure des logements diagnostiqués (modèle de DPE, type de bâtiment, période de construction) et calcule le taux de passoires attendu pour cette structure.
- comparaisons_ecoquartiers.py : le code contenu dans ce fichier vise à compléter la comparaison des écoquartiers avec le reste de la France (par label, par classe énergie et GES).
- utils_ecoquartiers.py : fonctions et paramètres communs aux scripts sur les écoquartiers (accès à S3, comptages par groupe, intervalles de confiance). Il n'est pas à lancer. *Bucket à paramétrer : `S3_BUCKET`, qui couvre tous les scripts sur les écoquartiers (`stat_desc_ecoquartiers.py`, `structure_logement_ecoquartiers.py`, `comparaisons_ecoquartiers.py`).*

Le fichier **references.bib** regroupe des références bibliographiques qui permettent de se constituer une culture liée au DPE. 

## Tout relancer depuis les données brutes (facultatif)

Lancer depuis la racine du dépôt (`python scripts/nom_du_script.py`), dans cet ordre :

1. `data_extraction_ademe.py`, puis `data_cleaning_ademe.py`.
2. L'archive des écoquartiers (`opendata-les-ecoquartiers.zip`) est déjà disponible dans le dossier public `diffusion` du bucket `cbouyer`, et `data_extraction_ecoquartiers.py` la récupère automatiquement. Étape manuelle uniquement si vous changez de bucket : télécharger l'archive sur [le site du ministère](https://www.ecoquartiers.logement.gouv.fr/document/opendata-les-ecoquartiers/) et la déposer, sans la décompresser, dans le dossier `diffusion` de votre bucket sous ce même nom.
3. `data_extraction_ecoquartiers.py`, puis `data_cleaning_ecoquartiers.py`.
4. `croisement_dpe_ecoquartiers.py` : il produit `resultats/dpe_ecoquartiers.parquet` en local, nécessaire aux scripts suivants.
5. `stat_desc.py`, `stat_desc_ecoquartiers.py`, `structure_logement_ecoquartiers.py` et `comparaisons_ecoquartiers.py` (dans n'importe quel ordre).
6. `quarto render rapport.qmd` (régénère `rapport.html`).