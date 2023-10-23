import json

station_trains = json.load(open("ML/Project/Data/rapid-api/station_trains.json"))

# The passing trains are the trains not originating and ending at the station. So all unique
adiOrigTrains = list(map(lambda d: d['trainNo'], station_trains['ADI']['data']['originating']))
adiPassingTrains = list(map(lambda d: d['trainNo'], station_trains['ADI']['data']['passing']))
print(len(set(adiOrigTrains)-set(adiPassingTrains)))

# 2548 unique true trains
allTrainNumbers = set()
for station, val in station_trains.items():
    trains = val['data']
    for trainDict in trains.values():
        allTrainNumbers = allTrainNumbers.union(set(map(lambda d: d['trainNo'], trainDict)))