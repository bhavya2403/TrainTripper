from utils import *
import json
from time import time
from random import shuffle
from datetime import datetime, timedelta

dynamic_pricing_trains = ['12298', '12283', '12278', '12297', '20840', '12269', '12274', '12282', '12310', '12306',
                        '12294', '12293', '12290', '12284', '12302', '12289', '12270', '12314', '12286', '12313',
                          '12281', '12301', '12285', '20839', '12309', '12305']

lines = open("./Data/irctc/schedules.json").readlines()
schedules = list(map(json.loads, lines))
train_numbers = list(map(lambda sch: sch['trainNumber'], schedules))
static_pricing_trains = list(set(train_numbers)-set(dynamic_pricing_trains))

shuffle(static_pricing_trains)
query_train_numbers = static_pricing_trains[:(300-len(dynamic_pricing_trains))]+dynamic_pricing_trains
shuffle(query_train_numbers)

query_schedules = list(filter(lambda sch: sch['trainNumber'] in query_train_numbers, schedules))
n = len(schedules)

irctcClient = IRCTCClient()
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)

file_number = 4
# queriesFile = open(f"./Data/irctc/queries{file_number}.json", 'a')
classCodes = ['1A', '2A', '3A', 'SL', '2S', 'CC']
start = time()
trainClasses = {}
for i, schedule in enumerate(schedules[3290:3291]):
    end=time()
    print(f"\rPercentage done: {round(i/n * 100, 2)}%, "
          f"time taken: {int(end-start)}s, time left: "
          f"{int((n-i-1)*(end-start)/(i+1))}s", end='')

    startDate = datetime.now().date() + timedelta(5)
    trainDepDate = None
    dateDiff = 0
    while not trainDepDate:
        date = startDate + timedelta(dateDiff)
        if schedule[f'trainRunsOn{date.strftime("%A")[:3]}']=='Y':
            trainDepDate = date.strftime("%Y%m%d")
        dateDiff += 1

    source_station = schedule['stationList'][0]['stationCode']
    dest_station = schedule['stationList'][-1]['stationCode']
    trainClasses[schedule['trainNumber']] = []
    for classCode in classCodes:
        response = fareInquiryClient.get(schedule['trainNumber'], trainDepDate,
                                         source_station, dest_station, classCode)
        if 'errorMessage' not in response:
            trainClasses[schedule['trainNumber']].append(classCode)
