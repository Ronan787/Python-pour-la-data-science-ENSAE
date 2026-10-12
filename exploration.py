
import pandas as pd
import numpy as np

# DPE depuis 2012
size = 1000 #nombre de lignes
code_region_ban = 11 #Ile de France
annee = 2022 # année d'établissement du DPE
url = 'https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines?size={}&format=csv&code_region_ban_eq={}&date_etablissement_dpe_gte={}-01-01&date_etablissement_dpe_lte={}-12-31'.format(size, code_region_ban, annee, annee)
df = pd.read_csv(url, sep = ',')
n_row, n_col = df.shape

print(n_row, n_col)