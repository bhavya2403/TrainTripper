from utils import *
from random import choices, randint
from datetime import timedelta, datetime
from time import time
import pandas as pd
import json

irctcClient = IRCTCClient()
trainListClient = TrainListClient(irctcClient)
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)
scheduleClient = TrainScheduleClient(irctcClient)

schedule_file = open("ML/Project/Data/irctc/schedules.json", 'r')
schedulesStr = schedule_file.readlines()
schedules = []
for str in schedulesStr:
    try:
        schedules.append(json.loads(str))
    except:
        continue

# -----------------------------------------------------------------------------------
# Aim is to use each train schedule to collect data, price dependence on distance change

queriesFile = open("ML/Project/Data/irctc/queries1.json", 'a')
classCodes = ['1A', '2A', '3A', 'SL', '2S', 'CC']
n = len(schedules)
start = time()
for i, schedule in enumerate(schedules):
    end=time()
    print(f"Percentage done: {round(i/n * 100, 2)}%, "
          f"time taken: {int(end-start)}s, time left: "
          f"{int((n-i-1)*(end-start)/(i+1))}s")

    timestamp=datetime.now()
    startDate = timestamp.date() + timedelta(randint(1, 110))
    for dateDiff in range(7):
        date = startDate + timedelta(dateDiff)
        dateStr = date.strftime("%Y%m%d")

        allStationCombinations = []
        for i in range(len(schedule['stationList'])):
            for j in range(i+1, len(schedule['stationList'])):
                allStationCombinations.append((schedule['stationList'][i]['stationCode'], schedule['stationList'][j][
                    'stationCode']))

        for source_station, dest_station in choices(allStationCombinations, k=10):
            for classCode in classCodes:
                response = fareInquiryClient.get(schedule['trainNumber'], dateStr,
                                                 source_station, dest_station, classCode)
                if 'errorMessage' not in response:
                    response['trainNumber'] = schedule['trainNumber']
                    response['fromStnCode'] = source_station
                    response['toStnCode'] = dest_station
                    response['classCode'] = classCode
                    response['timeStamp'] = f'{timestamp}'
                    json.dump(response, queriesFile)
                    queriesFile.write("\n")
                print(f"\rInquired for "
                      f"{schedule['trainName']}, {date}, {source_station}, {dest_station}, {classCode}, "
                      f"response: {response['errorMessage'][:-1] if 'errorMessage' in response else 'YES'}", end='')

queriesFile.close()