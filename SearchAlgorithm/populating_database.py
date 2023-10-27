import json
import datetime
from time import time
import pandas as pd
from sqlalchemy import create_engine
from urllib.parse import quote_plus
from decouple import config

lines = open("ML/Project/Data/irctc/schedules.json").readlines()
schedules = list(map(json.loads, lines))
engine = create_engine("postgresql://postgres:%s@localhost:5432/traintripper" % quote_plus(config("POSTGRES_PASSWORD")))

# Train schedules
stationLists = []
for train in schedules:
    for sch in train['stationList']:
        sch['trainNumber'] = train['trainNumber']
    stationLists += train['stationList']

schedulesDF = pd.DataFrame(stationLists)
schedulesDF.drop(['stnSerialNumber', 'boardingDisabled'], axis=1, inplace=True)

schedulesDF = schedulesDF.astype({'routeNumber': int, 'distance': int, 'dayCount': int, 'trainNumber': int})

schedulesDF['arrivalTime'] = pd.to_datetime(schedulesDF.arrivalTime, errors='coerce', format='%H:%M').dt.time
schedulesDF['departureTime'] = pd.to_datetime(schedulesDF.departureTime, errors='coerce', format='%H:%M').dt.time

def haltTimeConverter(string):
    if string == '--':
        return -1
    hours, mins = map(int, string.split(":"))
    return hours*60+mins
schedulesDF['haltTimeMinutes'] = schedulesDF.haltTime.apply(haltTimeConverter)
schedulesDF.drop(['haltTime'], axis=1, inplace=True)

start = time()
n = len(schedulesDF)
for i, row in schedulesDF.iterrows():
    arrTime = 'null' if row['arrivalTime']!=row['arrivalTime'] else f"'{row['arrivalTime']}'"
    depTime='null' if row['departureTime']!=row['departureTime'] else f"'{row['departureTime']}'"
    engine.execute(f"insert into trainschedules values ('{row['stationCode']}',"
                   f"{arrTime}, {depTime}, {row['routeNumber']}, {row['distance']},"
                   f"{row['dayCount']}, {row['trainNumber']}, {row['haltTimeMinutes']});")

    end=time()
    if not i%100:
        print(f"\rPercentage done: {round(i / n * 100, 2)}%, "
              f"time taken: {int(end - start)}s, "
              f"time left: {int((n - i - 1) * (end - start) / (i + 1))}s", end='')

# Trains
trainsDF = pd.DataFrame(schedules)
trainsDF.drop(['duration', 'stationList', 'timeStamp', 'trainOwner'], axis=1, inplace=True)
trainsDF.replace({'Y': 1, 'N': 0}, inplace=True)
# train-number, name, from station, to station, trainRunsOn{Day}

for i, row in trainsDF.iterrows():
    row['trainNumber'] = int(row['trainNumber'])
    insertString = f"insert into trains values {tuple(row)}"
    insertString = insertString.replace(", 1", ", true").replace(", 0", ", false")
    engine.execute(insertString)

# Stations
stationsDF = schedulesDF[['stationCode', 'stationName']].drop_duplicates(subset='stationCode')
for i, row in stationsDF.iterrows():
    engine.execute(f"insert into stations values {tuple(row)};")

# nearby stations
idx = 0
for sch in schedules:
    idx += 1
    if not idx%100:
        print(idx)
    for i in range(len(sch['stationList'])-1):
        stationJson = sch['stationList'][i]
        if stationJson['departureTime'] == '--':
            continue
        sourceStation = stationJson['stationCode']
        dayCount1 = int(stationJson['dayCount'])
        distance1 = int(stationJson['distance'])
        routeNumber1 = int(stationJson['routeNumber'])
        time1dt = datetime.datetime.strptime(stationJson['departureTime'], '%H:%M').time()
        for j in range(i+1, len(sch['stationList'])):
            stationJsonj = sch['stationList'][j]
            if stationJsonj['arrivalTime'] == '--':
                continue
            destinationStation = stationJsonj['stationCode']
            dayCount2 = int(stationJsonj['dayCount'])
            distance2=int(stationJsonj['distance'])
            routeNumber2 = int(stationJsonj['routeNumber'])
            time2dt = datetime.datetime.strptime(stationJsonj['arrivalTime'], '%H:%M').time()
            if routeNumber1!=routeNumber2:
                break

            diffInMinutes = (dayCount2-dayCount1)*1440 +\
                            (time2dt.hour-time1dt.hour)*60 + (time2dt.minute-time1dt.minute)
            if diffInMinutes > 60 or distance2-distance1>30:
                break
            engine.execute(f"insert into nearbystations values ('{sourceStation}', '{destinationStation}', "
                           f"{distance2-distance1});")
            engine.execute(f"insert into nearbystations values ('{destinationStation}', '{sourceStation}', "
                           f"{distance2 - distance1});")