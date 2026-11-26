# Les écoquartiers à l’épreuve des DPE
## Projet Python — ENSAE
Contributeurs : Maëlle Boutbal - Capucine Bouyer - Ronan Gorin

Ce projet vise à déterminer si les données mettent en évidence de meilleures performances énergétiques dans les écoquartiers, ou si leur exemplarité affichée doit être nuancée.
Les résultats obtenus sont regroupés dans un rapport qui prend la forme d'un site web Quarto Markdown. 

Les librairies python nécessaires à l'exécution du code sont regroupées dans le fichier **requirements.txt**. Elles peuvent être installées grâce à la ligne suivante : **pip install -r requirements.txt**
Pour ouvrir le site web Quarto Markdown, il suffit d'écrire la ligne suivante : "**quarto preview rapport.qmd**" dans le terminal de VS code après avoir ouvert le projet depuis GitHub. 

Le code lié au site Quarto Markdown se trouve dans le fichier **rapport.qmd**.
Une fois ouvert, il constitue le rapport qui synthétise nos résultats. 
 
Nous avons fait en sorte de réduire le temps de lancement du site au maximum. Pour ce faire nous avons notamment utilisé l’espace de stockage du SSPCloud. Les cartes et les graphiques utilisés dans le rapport ont été généré au préalable à partir du code, enregistrés et intégrés par la suite au rapport. Malgré ces précautions, le site met un certain temps à se lancer.

Les cartes générées sont interactives. Il est donc possible de naviguer dessus. Cependant, notre étude se limite à la France métropolitaine. Nul besoin d'explorer d'autres continents. 

Le code est regroupé dans le fichier **scripts**. 
- data_extraction_ademe.py : contient le code permettant de récupérer les données DPE depuis l'API associée. Les données ainsi récupérées sont stockées dans l’espace de stockage du SSPCloud. 
- data_cleaning_ademe.py : contient le code permettant de nettoyer et de formater les données DPE extraites, appelées depuis l’espace de stockage du SSPCloud.
- stat_desc.py : contient le code générant cartes et graphiques en vue d'obtenir des statistiques descriptives à l'échelle nationale. 
- data_extraction_insee.py : contient le code permettant de récupérer les données carroyées à 200 m de Filosofi 2021. Cette piste n'a finalement pas été retenue pour le rendu final.
- data_extraction_ecoquartiers.py : contient les données permettant de récupérer les contours des ÉcoQuartiers et de les charger dans un GeoDataFrame.
- data_cleaning_ecoquartiers.py : contient le code permettant de nettoyer les données des ÉcoQuartiers extraites. 
- croisement_dpe_ecoquartiers.py : contient le code permettant de croiser les DPE (points géographiques) avec les périmètres des ÉcoQuartiers. Le tout étant de savoir quels DPE se trouvent dans quel écoquartier.
- stat_desc_ecoquartiers.py : contient le code permettant de réaliser des statistiques descriptives sur les écoquartiers et de les comparer au reste de la France. 
- comparaison_ecoquartiers.py : le code contenu dans ce fichier vise à compléter la comparaison des écoquartiers avec le reste de la France.

Le fichier **references.bib** regroupe des références bibliographiques qui permettent de se constituer une culture liée au DPE. 
