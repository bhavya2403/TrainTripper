import json
import pandas as pd

schedules = json.load(open("ML/Project/Data/railways-master/schedules.json"))
df = pd.DataFrame(schedules)

uniqueTrains = df.train_number.unique()
print(len(list(uniqueTrains)))
# 5208 trains

for (trainNo, schedule_df) in df.groupby(["train_number"]):
    schedule_df.sort_values(['day', 'arrival'], inplace=True)