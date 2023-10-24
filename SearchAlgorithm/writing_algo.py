from datetime import datetime, date, timedelta
import pandas as pd
from sqlalchemy import create_engine
from urllib.parse import quote_plus
from decouple import config

engine = create_engine("postgresql://postgres:%s@localhost:5432/traintripper" % quote_plus(config("POSTGRES_PASSWORD")))

def trainsHaltingOn(stations):
    trains = pd.read_sql(f"select * from trainschedules where station_code in {tuple(stations)};", engine)
    return trains.drop(['route_number', 'halt_time_minutes'], axis=1)

def ifTrainDepOnDate(trainsDF, trainNumName, dateSeries: pd.Series):
    schedulesDF=pd.read_sql(f"select * from trains "
                            f"where train_number in {tuple(trainsDF[trainNumName].to_list())}", engine)
    schedulesDF.drop(['station_from', 'station_to'], axis=1, inplace=True)
    trainsWithDaysDF = trainsDF.merge(schedulesDF, 'left', left_on=trainNumName, right_on='train_number')
    daySeries = dateSeries.apply(lambda dt: dt.strftime('%A')[:3].lower())
    return trainsWithDaysDF.apply(lambda row: row[f'trainrunson{daySeries[row.name]}'], axis=1)

def ifTrainRunsOnDaycDate(trainsDF, trainNumName, dayCountName, dt: (date, pd.Series)):
    dtConv = dt
    if type(dt)==date:
        dtConv = pd.Series([dt]*len(trainsDF))
    reqDepDateSeries = trainsDF.apply(lambda row: dtConv[row.name]-timedelta(row[dayCountName]-1), axis=1)
    return ifTrainDepOnDate(trainsDF, trainNumName, reqDepDateSeries)

def getNearby(station):
    return pd.read_sql(f"select station2, distance from nearbystations where station1='{station}';", engine)

def filterBestNearForTrain(trainsDF, station):
    nearbyStationDist = getNearby(station)
    trainsWithStationDist = trainsDF.merge(nearbyStationDist, 'left', left_on='station_code', right_on='station2')
    trainsWithStationDist.sort_values(['distance_y'], inplace=True)
    trainsWithStationDist.drop_duplicates(subset='train_number', inplace=True)
    return trainsWithStationDist.drop(['station2', 'distance_y'], axis=1)

# From given source and destination considering nearby stations, single-journey
def trainsOnDateRunningNearby(sourceStation, destinationStation, dt):
    departingTrains = trainsHaltingOn(getNearby(sourceStation).station2.to_list())
    departingOnDateTrains = departingTrains[ifTrainRunsOnDaycDate(departingTrains, 'train_number', 'day_count', dt)]
    bestDepartingOnDateTrains = filterBestNearForTrain(departingOnDateTrains, sourceStation)
    arrivingTrains = trainsHaltingOn(getNearby(destinationStation).station2.to_list())
    bestArrivingTrains = filterBestNearForTrain(arrivingTrains, destinationStation)

    common = bestDepartingOnDateTrains.merge(bestArrivingTrains, 'inner', 'train_number')
    onlySourceToDest=common[common.distance_x_x < common.distance_x_y]
    return onlySourceToDest

# Fixed source and destination and not considering nearby stations, only one itinerary per train
def trainsOnDateRunningBetween(sourceStation, destinationStation, dt):
    trains = trainsOnDateRunningNearby(sourceStation, destinationStation, dt)
    return trains[(trains.station_code_x==sourceStation) & (trains.station_code_y==destinationStation)]

# Change one train only, fixed source and destination
def multiTrainItinaries(sourceStation, destinationStation, dt):
    departingTrains=trainsHaltingOn(getNearby(sourceStation).station2.to_list())
    departingOnDateTrains = departingTrains[ifTrainRunsOnDaycDate(departingTrains, 'train_number', 'day_count', dt)]
    bestDepartingOnDateTrains = filterBestNearForTrain(departingOnDateTrains, sourceStation)
    # create a column with next halting stops along with their time
    departingTrainSchedules = pd.read_sql(f"select * from trainschedules where train_number in "
                                          f"{tuple(bestDepartingOnDateTrains.train_number.to_list())}", engine)
    departingTrainSchedules.drop(['halt_time_minutes', 'route_number'], axis=1, inplace=True)
    stationPairsDF = bestDepartingOnDateTrains.merge(departingTrainSchedules, 'right', 'train_number')
    departingHaltingPairs = stationPairsDF[(stationPairsDF.day_count_y>stationPairsDF.day_count_x) |
                   (
                        (stationPairsDF.day_count_y == stationPairsDF.day_count_x) &
                        (stationPairsDF.departure_time_x < stationPairsDF.arrival_time_y)
                    )]
    departingHaltingPairs['halt_arrival'] = departingHaltingPairs.apply(lambda row:
                dt+timedelta(row['day_count_y']-row['day_count_x']), axis=1)

    arrivingTrains=trainsHaltingOn(getNearby(destinationStation).station2.to_list())
    bestArrivingTrains = filterBestNearForTrain(arrivingTrains, destinationStation)
    arrivingTrainSchedules = pd.read_sql(f"select * from trainschedules where train_number in "
                                          f"{tuple(bestArrivingTrains.train_number.to_list())}", engine)
    arrivingTrainSchedules.drop(['halt_time_minutes', 'route_number'], axis=1, inplace=True)
    stationPairsDF = bestArrivingTrains.merge(arrivingTrainSchedules, 'right', 'train_number')
    haltingArrivingPairs = stationPairsDF[(stationPairsDF.day_count_y < stationPairsDF.day_count_x) |
                                          (
                        (stationPairsDF.day_count_y == stationPairsDF.day_count_x) &
                        (stationPairsDF.arrival_time_x > stationPairsDF.departure_time_y)
                    )]

    allComb = departingHaltingPairs.merge(haltingArrivingPairs, 'inner', 'station_code_y')
    allComb = allComb[allComb.train_number_x!=allComb.train_number_y]
    allComb.reset_index(inplace=True)

    train2OnDateOnHalt = ifTrainRunsOnDaycDate(allComb, 'train_number_y', 'day_count_y_y', allComb['halt_arrival']) \
                         & (allComb.departure_time_y_y > allComb.arrival_time_y_x)
    train2OnNextDateHalt = ifTrainRunsOnDaycDate(allComb, 'train_number_y', 'day_count_y_y',
                                                 allComb['halt_arrival']+timedelta(1))
    lessLayoverTrains = allComb[train2OnDateOnHalt|train2OnNextDateHalt]
    lessLayoverTrains['halt_departure'] = lessLayoverTrains.apply(lambda row:
                    row['halt_arrival'] if train2OnDateOnHalt[row.name] else row['halt_arrival']+timedelta(1), axis=1)
    lessLayoverTrains['halt_time_minutes'] = lessLayoverTrains.apply(lambda row:
             (datetime.combine(row['halt_departure'], row['departure_time_y_y']) -
              datetime.combine(row['halt_arrival'], row['arrival_time_y_x'])).total_seconds() / 60, axis=1)

    lessLayoverTrains.sort_values('halt_time_minutes', ascending=False, inplace=True)
    lessLayoverTrains.drop_duplicates(subset=['train_number_x', 'train_number_y'], inplace=True)

    lessLayoverTrains['reach_date'] = lessLayoverTrains.apply(lambda row:
            row['halt_departure']+timedelta(row['day_count_x_y']-row['day_count_y_y']), axis=1)
    lessLayoverTrains['journey_time_minutes'] = lessLayoverTrains.apply(lambda row:
            (datetime.combine(row['reach_date'], row['arrival_time_x_y'])-
             datetime.combine(dt, row['departure_time_x_x'])).total_seconds()/60, axis=1)

    return lessLayoverTrains.sort_values('journey_time_minutes').head(100)

    # find the arrival date and time on halting station.
        # x_x -  source station train1
        # y_x -  halt station train1
        # x_y -  destination station train2
        # y_y -  halt station train2

def main():
    sourceStation = 'CAPE'
    destinationStation = 'DBRG'
    dt = date(2023, 10, 26)
    df = trainsOnDateRunningNearby(sourceStation, destinationStation, dt)
    df1 = trainsOnDateRunningBetween(sourceStation, destinationStation, dt)
    df2 = multiTrainItinaries(sourceStation, destinationStation, dt)
