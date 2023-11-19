from utils import *
import json
from time import time
from datetime import datetime, timedelta

lines = open("./Data/irctc/schedules.json").readlines()
schedules = list(map(json.loads, lines))

irctcClient = IRCTCClient()
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)

classCodes = ['1A', '2A', '3A', 'SL', '2S', 'CC']
trainClasses = {}
n = len(schedules)
start = time()
for i, schedule in enumerate(schedules):
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
        response = fareInquiryClient.get(schedule['trainNumber'], trainDepDate, source_station, dest_station, classCode)
        if 'errorMessage' not in response:
            trainClasses[schedule['trainNumber']].append(classCode)