# Les écoquartiers à l’épreuve des DPE
## Projet Python — ENSAE
Contributeurs : Maëlle Boutbal - Capucine Bouyer - Ronan Gorin

Ce projet vise à déterminer si les données mettent en évidence de meilleures performances énergétiques dans les écoquartiers, ou si leur exemplarité affichée doit être nuancée.
Les résultats obtenus sont regroupés dans un rapport qui prend la forme d'un site web Quarto Markdown, dont le code se trouve dans le fichier **rapport.qmd**.

## Ouvrir le rapport

Les librairies python nécessaires sont regroupées dans le fichier **requirements.txt** : **pip install -r requirements.txt**
Pour ouvrir le site web, il suffit d'écrire la ligne suivante dans le terminal de VS code après avoir ouvert le projet depuis GitHub : "**quarto preview rapport.qmd**". Pour créer un fichier `rapport.html`, utiliser `quarto render rapport.qmd` : il doit alors être conservé avec son dossier `rapport_files/`.

Nous avons fait en sorte de réduire le temps de lancement du site au maximum : les cartes et les graphiques ont été générés au préalable à partir du code et enregistrés dans l’espace de stockage du SSPCloud, que le rapport se contente d'afficher. Malgré ces précautions, le site met un certain temps à se lancer.

Les cartes générées sont interactives. Il est donc possible de naviguer dessus. Cependant, notre étude se limite à la France métropolitaine. Nul besoin d'explorer d'autres continents. 

## Les scripts

Le code est regroupé dans le dossier **scripts**. Il n'est pas nécessaire de le relancer pour ouvrir le rapport.
- data_extraction_ademe.py : contient le code permettant de récupérer les données DPE depuis l'API associée. Les données ainsi récupérées sont stockées dans l’espace de stockage du SSPCloud (2 à 3 heures).
- data_cleaning_ademe.py : contient le code permettant de nettoyer et de formater les données DPE extraites, appelées depuis l’espace de stockage du SSPCloud.
- stat_desc.py : contient le code générant cartes et graphiques en vue d'obtenir des statistiques descriptives à l'échelle nationale. 
- data_extraction_ecoquartiers.py : contient le code permettant de récupérer les contours des ÉcoQuartiers et de les charger dans un GeoDataFrame.
- data_cleaning_ecoquartiers.py : contient le code permettant de nettoyer les données des ÉcoQuartiers extraites. 
- croisement_dpe_ecoquartiers.py : contient le code permettant de croiser les DPE (points géographiques) avec les périmètres des ÉcoQuartiers. Le tout étant de savoir quels DPE se trouvent dans quel écoquartier.
- stat_desc_ecoquartiers.py : contient le code permettant de réaliser des statistiques descriptives sur les écoquartiers et de les comparer au reste de la France. 
- structure_logement_ecoquartiers.py : compare la structure des logements diagnostiqués (modèle de DPE, type de bâtiment, période de construction) et calcule le taux de passoires attendu pour cette structure.
- comparaisons_ecoquartiers.py : le code contenu dans ce fichier vise à compléter la comparaison des écoquartiers avec le reste de la France (par label, par classe énergie et GES).
- utils_ecoquartiers.py : fonctions et paramètres communs aux scripts sur les écoquartiers (accès à S3, comptages par groupe, intervalles de confiance). Il n'est pas à lancer.

Le fichier **references.bib** regroupe des références bibliographiques qui permettent de se constituer une culture liée au DPE. 

## Tout relancer depuis les données brutes (facultatif)

Lancer depuis la racine du dépôt (`python scripts/nom_du_script.py`), dans cet ordre :

1. `data_extraction_ademe.py`, puis `data_cleaning_ademe.py`.
2. **Étape manuelle** : télécharger l'archive des écoquartiers sur [le site du ministère](https://www.ecoquartiers.logement.gouv.fr/document/opendata-les-ecoquartiers/) et la déposer, sans la décompresser, dans le dossier `diffusion` du bucket S3 sous le nom `opendata-les-ecoquartiers.zip`.
3. `data_extraction_ecoquartiers.py`, puis `data_cleaning_ecoquartiers.py`.
4. `croisement_dpe_ecoquartiers.py` : il produit `resultats/dpe_ecoquartiers.parquet` en local, nécessaire aux scripts suivants.
5. `stat_desc.py`, `stat_desc_ecoquartiers.py`, `structure_logement_ecoquartiers.py` et `comparaisons_ecoquartiers.py` (dans n'importe quel ordre).
6. `quarto render rapport.qmd`.

**Paramètres à adapter.** Avec un autre compte, remplacer les noms de buckets (il faut un droit d'écriture) : `S3_BUCKET` dans `data_extraction_ademe.py`, `data_cleaning_ademe.py` et `utils_ecoquartiers.py`, `BUCKET_R` dans `stat_desc.py`, et `DPE_CLEAN` dans `utils_ecoquartiers.py`. Les fichiers lus par `rapport.qmd` (dossier `diffusion` de chaque bucket) doivent être en **lecture publique**.