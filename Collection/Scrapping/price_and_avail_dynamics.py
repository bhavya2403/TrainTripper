from utils import *
import json
from time import time
from random import shuffle
from datetime import datetime, timedelta

dynamic_pricing_trains = ['12298', '12283', '12278', '12297', '20840', '12269', '12274', '12282', '12310', '12306',
                        '12294', '12293', '12290', '12284', '12302', '12289', '12270', '12314', '12286', '12313',
                          '12281', '12301', '12285', '20839', '12309', '12305']

lines = open("../../Data/irctc/schedules.json").readlines()
schedules = list(map(json.loads, lines))
train_numbers = list(map(lambda sch: sch['trainNumber'], schedules))
static_pricing_trains = list(set(train_numbers)-set(dynamic_pricing_trains))

shuffle(static_pricing_trains)
query_train_numbers = static_pricing_trains[:(300-len(dynamic_pricing_trains))]+dynamic_pricing_trains
shuffle(query_train_numbers)

query_schedules = list(filter(lambda sch: sch['trainNumber'] in query_train_numbers, schedules))
n = 300

irctcClient = IRCTCClient()
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)

file_number = 1
queriesFile = open(f"./Data/irctc/queries{file_number}.json", 'a')
classCodes = ['1A', '2A', '3A', 'SL', '2S', 'CC']
start = time()
for i, schedule in enumerate(query_schedules[(file_number-1)*60:file_number*60]):
    end=time()
    print(f"\rPercentage done: {round(i/n * 100, 2)}%, "
          f"time taken: {int(end-start)}s, time left: "
          f"{int((n-i-1)*(end-start)/(i+1))}s")

    timestamp = datetime.now()
    startDate = timestamp.date() + timedelta(5)
    trainDepDates = []
    dateDiff = 0
    while len(trainDepDates)<200:
        date = startDate + timedelta(dateDiff)
        if schedule[f'trainRunsOn{date.strftime("%A")[:3]}']=='Y':
            trainDepDates.append(date.strftime("%Y%m%d"))
        dateDiff += 1

    allComb = []
    for i in range(len(schedule['stationList'])):
        for j in range(i+1, len(schedule['stationList'])):
            allComb.append((schedule['stationList'][i]['stationCode'], schedule['stationList'][j]['stationCode']))
    shuffle(allComb)

    for source_station, dest_station in allComb[:min(10, len(allComb))]:
        for classCode in classCodes:
            availArr = []
            while len(availArr)<200:
                dateStr = trainDepDates[len(availArr)]
                response = fareInquiryClient.get(schedule['trainNumber'], dateStr,
                                                 source_station, dest_station, classCode)
                print(f"\rInquired for "
                      f"{schedule['trainName']}, {dateStr}, {source_station}, {dest_station}, {classCode}, "
                      f"response: {response['errorMessage'][:-1] if 'errorMessage' in response else 'YES'}", end='')
                if 'errorMessage' in response:
                    break
                availArr += response['availability']
                response['trainNumber'] = schedule['trainNumber']
                response['fromStnCode'] = source_station
                response['toStnCode'] = dest_station
                response['classCode'] = classCode
                response['timeStamp'] = f'{timestamp}'
                json.dump(response, queriesFile)
                queriesFile.write("\n")

queriesFile.close()