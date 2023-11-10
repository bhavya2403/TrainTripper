import re
import json
import pandas as pd
import matplotlib.pyplot as plt
from copy import deepcopy

df = pd.read_csv("./Data/irctc/dynamic_coverage.csv")

new_rows = []
for idx, row in df.iterrows():
    new_row = row.to_dict()
    avail_obj  = json.loads(new_row['availability'].replace("'", '"'))
    new_row.__delitem__("availability")
    for date_status in avail_obj:
        new_row_cpy = deepcopy(new_row)
        new_row_cpy['date'] = date_status['date']
        new_row_cpy['status'] = date_status['status']
        new_rows.append(new_row_cpy)
df_new = pd.DataFrame(new_rows)

# Find the combination with most date entries
grouped = df_new.groupby(['trainNumber', 'fromStnCode', 'toStnCode', 'classCode'])
mx_comb = grouped.size().max()
high_sz_group = grouped.get_group(mx_comb)

# both available and other status category in row'status' which was bad
for idx, row in df_new.iterrows():
    if 'AVAILABLE' in row['status'] and '/' in row['status']:
        df_new.loc[idx, 'status'] = 'AVAILABLE-0100'

# attempting to find unique status categories

values=set()
for idx, row in df_new.iterrows():
    if '/' in row['status']:
        val1, val2=row['status'].split('/')
        values.add(val1)
        values.add(val2)

unique_categories = set()
for value in values:
    s = ""
    for c in value:
        if c.isnumeric() or c==' ':
            break
        s += c
    unique_categories.add(s)
# {'RLWL', 'RSWL', 'PQWL', 'WL', 'GNWL', 'RAC', 'AVAILABLE'}

