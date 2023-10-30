from utils import *
from datetime import timedelta, datetime
from time import time
import json

irctcClient = IRCTCClient()
trainListClient = TrainListClient(irctcClient)
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)
scheduleClient = TrainScheduleClient(irctcClient)

schedule_file = open("./Data/irctc/schedules.json", 'r')
schedulesStr = schedule_file.readlines()
schedules = list(map(json.loads, schedulesStr))

# -----------------------------------------------------------------------------------
# Aim is to use each train schedule to collect data, static data collection. independent of date change

file_number = 11
queriesFile = open(f"./Data/irctc/queries{file_number}.json", 'a')
classCodes = ['1A', '2A', '3A', 'SL', '2S', 'CC']
n = 329
start = time()
for i, schedule in enumerate(schedules[(file_number-2)*329:(file_number-1)*329]):
    end=time()
    print(f"\rPercentage done: {round(i/n * 100, 2)}%, "
          f"time taken: {int(end-start)}s, time left: "
          f"{int((n-i-1)*(end-start)/(i+1))}s")

    timestamp = datetime.now()
    startDate = timestamp.date() + timedelta(5)
    date = None
    for dateDiff in range(7):
        date = startDate + timedelta(dateDiff)
        if schedule[f'trainRunsOn{date.strftime("%A")[:3]}']=='Y':
            break
    dateStr = date.strftime("%Y%m%d")

    for i in range(len(schedule['stationList'])):
        source_station = schedule['stationList'][i]['stationCode']
        for j in range(i+1, len(schedule['stationList'])):
            dest_station = schedule['stationList'][j]['stationCode']
            for classCode in classCodes:
                response = fareInquiryClient.get(schedule['trainNumber'], dateStr,
                                                 source_station, dest_station, classCode)
                print(f"\rInquired for "
                      f"{schedule['trainName']}, {date}, {source_station}, {dest_station}, {classCode}, "
                      f"response: {response['errorMessage'][:-1] if 'errorMessage' in response else 'YES'}", end='')
                if 'errorMessage' not in response:
                    response['trainNumber'] = schedule['trainNumber']
                    response['fromStnCode'] = source_station
                    response['toStnCode'] = dest_station
                    response['classCode'] = classCode
                    response['timeStamp'] = f'{timestamp}'
                    json.dump(response, queriesFile)
                    queriesFile.write("\n")

queriesFile.close()