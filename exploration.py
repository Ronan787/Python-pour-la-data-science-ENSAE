
import pandas as pd
import numpy as np

# DPE avant juillet 2021
size = 1000 #nombre de lignes
code_region_ban = 11 #Ile de France
# &code_region_ban_eq={}
annee = 2019 # année d'établissement du DPE
url = 'https://data.ademe.fr/data-fair/api/v1/datasets/dpe-france/lines?size={}&format=csv&date_etablissement_dpe_gte={}-01-01&date_etablissement_dpe_lte={}-12-31'.format(size, annee, annee)
df = pd.read_csv(url, sep = ',')
n_row, n_col = df.shape

print(n_row, n_col)