import json
from collections import Counter, defaultdict
import matplotlib.pyplot as plt
import pandas as pd

lines = open("ML/Project/Data/irctc/schedules.json").readlines()
schedules = list(map(json.loads, lines))

# ___________________________________________

# distribution of train numbers

trainNumbers = list(map(lambda d: int(d['trainNumber']), schedules))
histogram = plt.hist(trainNumbers, bins=10)
plt.title("Histogram for distribution of available train \nnumbers for public trains")
plt.ylabel("Count of train numbers")
plt.xlabel("Train numbers ranges")
plt.show()
# Almost no trains after 23000 train numbers

# _____________________________________________________

# Occurrences for each station

counter = Counter()
for sch in schedules:
    stations = sch['stationList'] if 'stationList' in sch else []
    codes = list(map(lambda d: d['stationCode'], stations))
    counter.update(codes)
# 3766 unique stations where at least one train stop

stationCountSeries = pd.Series(counter)
stationCountSeries.sort_values(inplace=True, ascending=False)
# 64% of the trains who have at most 10 unique trains stopping
# 78% have 20 unique trains stopping
# 187 stations have only 1 train stopping

common10 = stationCountSeries.head(10)
plt.bar(common10.index, common10)
plt.title("Bar plot for most common 10 stations")
plt.show()
# highest 350 unique stopping trains
# CNB, ET, BZA with the highest values

# ______________________________________________________

# check if I can create a dataframe with all source and destination stations
sourceDestCounter = defaultdict(int)
for sch in schedules:
    stations = sch['stationList'] if 'stationList' in sch else []
    codes = list(map(lambda d: d['stationCode'], stations))
    for i in range(len(codes)):
        for j in range(i+1, len(codes)):
            sourceDestCounter[(codes[i], codes[j])] += 1
print(sum(sourceDestCounter.values()))

sourceDestSeries = pd.Series(sourceDestCounter.values(), index=sourceDestCounter.keys())
sourceDestSeries.sort_values(inplace=True, ascending=False)
# 80% source destination station combinations have only 2 trains between them
# 97% with only 10

common10 = sourceDestSeries.head(10)
plt.bar(list(map(lambda t: str(t), common10.index)), common10)
plt.xticks(rotation=45)
plt.title("Bar plot for most common 10 station combinations")
plt.show()
# Ahmedabad-Baroda-Surat most common combinations for trains
# Khurda-Bhuvaneshvar