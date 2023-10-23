import pandas as pd

df = pd.read_csv("ML/Project/Data/ogd/Train_details_22122017.csv")

# 8151 unique train stations
print(len(df['Station Code'].unique()))

# 11115 unique trains
print(len(df['Train No'].unique()))