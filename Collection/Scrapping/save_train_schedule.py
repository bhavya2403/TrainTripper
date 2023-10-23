from time import time
import pandas as pd
import json
from utils import *

irctcClient = IRCTCClient()
scheduleClient = TrainScheduleClient(irctcClient)

# trains of OGD data
df = pd.read_csv("ML/Project/Data/ogd/Train_details_22122017.csv")
trains = list(df["Train No"].unique())

# rapid-api trains
station_trains = json.load(open("ML/Project/Data/rapid-api/station_trains.json"))
allTrainNumbers = set()
for station, val in station_trains.items():
    trains = val['data']
    for trainDict in trains.values():
        allTrainNumbers = allTrainNumbers.union(set(map(lambda d: d['trainNo'], trainDict)))

# already saved
schedule_file = open("ML/Project/Data/irctc/schedules.json", 'r')
schedulesStr = schedule_file.readlines()
schedules = []
for str in schedulesStr:
    try:
        schedules.append(json.loads(str))
    except:
        continue
savedTrains = list(map(lambda d: int(d['trainNumber']), schedules))
schedule_file.close()

# --------------------------------------------------------------------------------------
# allTrainNumbers

schedule_file = open("ML/Project/Data/irctc/schedules.json", 'a')
start = time()
n = len(allTrainNumbers)
done = set()
for i, trainNo in enumerate(allTrainNumbers):
    schedule = scheduleClient.get(trainNo)
    if "errorMessage" not in schedule:
        json.dump(schedule, schedule_file)
        schedule_file.write("\n")

    done.add(trainNo)
    end=time()
    print(f"\rPercentage done: {round(i/n*100, 2)}%, "
          f"time taken: {int(end-start)}s, "
          f"time left: {int((n-i-1)*(end-start)/(i+1))}s, "
          f"current train: {trainNo}, "
          f"got data: {schedule['errorMessage'] if 'errorMessage' in schedule else 'YES'}", end='')

schedule_file.close()