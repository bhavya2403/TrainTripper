from utils import *
from random import randint
from datetime import timedelta, datetime
import json
from time import time

station_codes = ["NDLS", "HWH", "BCT", "CSTM", "MAS", "SC", "PNBE", "BZA", "ADI", "SBC", "YPR", "KGP", "HWH", "PUNE", "KYN", "CSTM", "NDLS", "LTT", "GZB", "ASR", "JP", "BSB", "BRC", "ADI", "MAS", "PPI", "NZM", "SC", "UBL", "JHS", "RJT", "TVC", "VSKP", "HWH", "MAS", "NDLS", "PNBE", "BZA", "ADI", "SBC", "PUNE", "KGP", "YPR", "GZB", "ASR"]

irctcClient = IRCTCClient()
trainListClient = TrainListClient(irctcClient)
fareInquiryClient = TrainPriceAndAvailClient(irctcClient)
scheduleClient = TrainScheduleClient(irctcClient)

# -----------------------------------------------------------------------------------
# Aim is to use train between stations to collect data, gather all available train numbers

m = len(station_codes)
n = m*(m-1) - (m-1)
queriesFile = open("ML/Project/Data/irctc/queries.json", 'a')

start = time()
i = 0
for j in range(1, m+1):
    for k in range(m):
        if j == k:
            continue
        i += 1
        end=time()
        print(f"Percentage done: {round(i / n * 100, 2)}%, "
              f"time taken: {int(end-start)}s, time left: "
              f"{int((n - i - 1) * (end - start) / (i + 1))}s")

        timestamp = datetime.now()
        date = timestamp.date() + timedelta(randint(1, 120))          # select one of the next 10 dates
        dateStr = date.strftime("%Y%m%d")

        trains = trainListClient.get(station_codes[j], station_codes[k], dateStr)
        for trainJson in trains:
            trainJson["pricesAndAvail"] = []
            for classCode in trainJson['avlClasses']:
                print(f"\rInquiring for {classCode} in train-{trainJson['trainName']} on date-{dateStr} between "
                      f"stations {trainJson['fromStnCode']} and {trainJson['toStnCode']}", end='')
                classPriceAvail = fareInquiryClient.get(trainJson['trainNumber'], dateStr,
                                                        trainJson['fromStnCode'], trainJson['toStnCode'], classCode)
                classPriceAvail['class'] = classCode
                trainJson["pricesAndAvail"].append(classPriceAvail)

            trainJson['timeStamp'] = f'{timestamp}'
            json.dump(trainJson, queriesFile)
            queriesFile.write("\n")
        print()

queriesFile.close()