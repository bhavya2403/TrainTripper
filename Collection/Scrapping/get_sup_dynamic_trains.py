from Collection.Scrapping.utils import *
import json
from datetime import datetime, timedelta
from time import time

lines = open("./Data/irctc/schedules.json", 'r').readlines()
schedules = list(map(json.loads, lines))

irctcClient = IRCTCClient()
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)

train_classes = json.load(open("Data/precomputes/train_classes"))
superfast_charge = {}
catering_trains = set()
dynamic_trains = set()
n = 329
start = time()
for i, schedule in enumerate(schedules[2632:2961]):
    tn = schedule['trainNumber']
    if not len(train_classes[tn]):
        continue

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
    for classCode in train_classes[tn]:
        response=fareInquiryClient.get(tn, trainDepDate, source_station, dest_station, classCode)
        if 'errorMessage' in response:
            continue
        if int(response['superfastCharge']):
            if tn not in superfast_charge:
                superfast_charge[tn] = {}
            superfast_charge[tn][classCode]=int(response['superfastCharge'])
        if int(response['dynamicFare']):
            dynamic_trains.add((tn, classCode))
        if int(response['cateringCharge']):
            catering_trains.add((tn, classCode))

json.dump(superfast_charge, open(f"Data/precomputes/superfast_charge", 'w'))
json.dump(list(catering_trains), open(f"Data/precomputes/catering_trains.json", 'w'))
json.dump(list(dynamic_trains), open(f"Data/precomputes/dynamicfare_trains.json", 'w'))
