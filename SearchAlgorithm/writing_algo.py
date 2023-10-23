import datetime
import pandas as pd
from sqlalchemy import create_engine
from urllib.parse import quote_plus
from decouple import config

engine = create_engine("postgresql://postgres:%s@localhost:5432/traintripper" % quote_plus(config("POSTGRES_PASSWORD")))


def trainsHaltingOn(stations):
    if len(stations)==1:
        return pd.read_sql(f"select * from trainschedules where station_code='{stations[0]}'", engine)
    return pd.read_sql(f"select * from trainschedules where station_code in {tuple(stations)};", engine)

def trainsRunningBetween(sourceStation, destinationStation, fixedSourceDest=True):
    departingTrains = trainsHaltingOn(
        [sourceStation] if fixedSourceDest else getNearby(sourceStation).station2.to_list()
    )
    arrivingTrains = trainsHaltingOn(
        [destinationStation] if fixedSourceDest else getNearby(destinationStation).station2.to_list()
    )
    common = pd.merge(departingTrains, arrivingTrains, 'inner', 'train_number')
    onlySourceToDest = common[common.distance_x < common.distance_y]
    return onlySourceToDest

def filterTrainsByDate(trainsDF, date):
    schedulesDF = pd.read_sql("select * from trains", engine)
    trainsWithDaysDF = pd.merge(trainsDF, schedulesDF, 'left', 'train_number')
    startDatesSeries = trainsWithDaysDF.day_count_x.apply(lambda n: date-datetime.timedelta(days=n-1))
    daySeries = startDatesSeries.apply(lambda dt: dt.strftime('%A')[:3].lower())
    trainsWithDaysDF['day'] = startDatesSeries.apply(lambda dt: dt.strftime('%A')[:3].lower())
    return trainsWithDaysDF[trainsWithDaysDF.apply(lambda row: row[f'trainrunson{daySeries[row.name]}'], axis=1)]

def getNearby(station):
    return pd.read_sql(f"select * from nearbystations where station1='{station}';", engine)

def trainsOnDateRunningBetween(sourceStation, destinationStation, date):
    return filterTrainsByDate(trainsRunningBetween(sourceStation, destinationStation), date)

############## From given source and destination considering nearby statinos, single-journey

def filterTrainWithBestItinary(trainsDF, sourceStation, destinationStation):
    nearbySource = getNearby(sourceStation)
    trainsSourceDistance = pd.merge(trainsDF, nearbySource, 'left', left_on='station_code_x', right_on='station2')
    trainsSourceDistance.sort_values(['distance'], inplace=True)
    trainsSourceDistance.drop_duplicates(subset='train_number', inplace=True)

    nearbyDestination = getNearby(destinationStation)
    trainsDestDistance = pd.merge(trainsDF, nearbyDestination, 'left', left_on='station_code_y', right_on='station2')
    trainsDestDistance.sort_values(['distance'], inplace=True)
    trainsDestDistance.drop_duplicates(subset='train_number', inplace=True)

    concatenated = pd.concat([trainsSourceDistance, trainsDestDistance], ignore_index=True)
    concatenated.drop_duplicates(subset=['train_number', 'station_code_x', 'station_code_y'], inplace=True)
    return concatenated

def trainsOnDateAlmostRunningBetween(sourceStation, destinationStation, date):
    trains = filterTrainsByDate(trainsRunningBetween(sourceStation, destinationStation, False), date)
    return filterTrainWithBestItinary(trains, sourceStation, destinationStation)

#################### Change one train only, fixed source and destination

def multiTrainItinaries(sourceStation, destinationStation, date):
    pass