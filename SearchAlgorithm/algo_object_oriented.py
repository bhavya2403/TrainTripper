import numpy as np
from datetime import date
import pandas as pd
from sqlalchemy import create_engine
from urllib.parse import quote_plus
from decouple import config

class TrainDatabase:
    def __init__(self, database_url):
        self.engine = create_engine(database_url)

    def trains_halting_on(self, stations):
        try:
            query = f"select * from trainschedules where station_code in {tuple(stations)};"
            if not len(stations):
                query = "select * from trainschedules where false;"
            elif len(stations)==1:
                stations.append(stations[0])
            return pd.read_sql(query, self.engine).drop(['route_number', 'halt_time_minutes'], axis=1)
        except Exception as e:
            print(f"Error fetching train schedules: {e}")
            return None

    def fetch_trains_schedules(self, train_numbers):
        try:
            query = f"select * from trainschedules where train_number in {tuple(train_numbers)};"
            if not len(train_numbers):
                query = "select * from trainschedules where false;"
            elif len(train_numbers)==1:
                train_numbers.append(train_numbers[0])
            return pd.read_sql(query, self.engine).drop(['route_number', 'halt_time_minutes'], axis=1)
        except Exception as e:
            print(f"Error fetching train schedules: {e}")
            return None

    def fetch_train_info(self, train_numbers):
        try:
            query = f"select * from trains where train_number in {tuple(train_numbers)}"
            if not len(train_numbers):
                query = "select * from trains where false;"
            elif len(train_numbers)==1:
                train_numbers.append(train_numbers[0])
            return pd.read_sql(query, self.engine)
        except Exception as e:
            print(f"Error fetching train info: {e}")
            return None

    def fetch_nearby_stations(self, station):
        try:
            query = f"select station2, distance from nearbystations where station1 = '{station}';"
            return pd.read_sql(query, self.engine)
        except Exception as e:
            print(f"Error fetching nearby stations: {e}")
            return None

    def check_station_exists(self, station):
        try:
            return len(pd.read_sql(f"select 1 from stations where station_code='{station}'", self.engine))
        except Exception as e:
            print(f"Error fetching stations table: {e}")
            return None

class TrainsFinder:
    def __init__(self, database_url):
        self.db = TrainDatabase(database_url)

    def __if_train_dep_on_date(self, trains_df, train_num_name, date_series):
        schedules_df = self.db.fetch_train_info(trains_df[train_num_name].to_list())
        schedules_df.drop(['station_from', 'station_to'], axis=1, inplace=True)
        trains_with_days_df = trains_df.merge(schedules_df, 'left', left_on=train_num_name, right_on='train_number')
        day_series = pd.to_datetime(date_series).dt.strftime('%A').str[:3].str.lower()
        return trains_with_days_df.to_numpy()[trains_with_days_df.index.get_indexer(trains_with_days_df.index),
            trains_with_days_df.columns.get_indexer('trainrunson'+day_series)]

    def _if_train_runs_on_dayc_date(self, trains_df, train_num_name, day_count_name, dt):
        dt_conv = dt if isinstance(dt, pd.Series) else pd.Series([dt] * len(trains_df))
        req_dep_date_series = pd.to_datetime(dt_conv)-pd.to_timedelta(trains_df[day_count_name]-1, unit='D')
        return self.__if_train_dep_on_date(trains_df, train_num_name, req_dep_date_series)

    def _filter_best_near_for_train(self, trains_df, station):
        nearby_station_dist = self.db.fetch_nearby_stations(station)
        trains_with_station_dist = trains_df.merge(nearby_station_dist, 'left', left_on='station_code', right_on='station2')
        trains_with_station_dist.sort_values(['distance_y'], inplace=True)
        trains_with_station_dist.drop_duplicates(subset='train_number', inplace=True)
        return trains_with_station_dist.drop(['station2', 'distance_y'], axis=1)

    def _trains_halting_on_nearby(self, station_code, dt=None):
        nearby_stations = self.db.fetch_nearby_stations(station_code).station2.to_list()
        trains_halting = self.db.trains_halting_on(nearby_stations)
        if dt:
            trains_halting = trains_halting[
                self._if_train_runs_on_dayc_date(trains_halting, 'train_number', 'day_count', dt)]
        return self._filter_best_near_for_train(trains_halting, station_code)

    def _add_next_stops(self, departing_trains):
        departing_train_schedules = self.db.fetch_trains_schedules(departing_trains.train_number.to_list())
        station_pairs_df = departing_trains.merge(departing_train_schedules, 'right', 'train_number')
        return station_pairs_df[(station_pairs_df.day_count_y > station_pairs_df.day_count_x) |
                               (
                                       (station_pairs_df.day_count_y == station_pairs_df.day_count_x) &
                                       (station_pairs_df.departure_time_x < station_pairs_df.arrival_time_y)
                               )]

    def _add_previous_stops(self, arriving_trains):
        arriving_train_schedules=self.db.fetch_trains_schedules(arriving_trains.train_number.to_list())
        station_pairs_df=arriving_trains.merge(arriving_train_schedules, 'right', 'train_number')
        return station_pairs_df[(station_pairs_df.day_count_y < station_pairs_df.day_count_x) |
                                (
                                        (station_pairs_df.day_count_y == station_pairs_df.day_count_x) &
                                        (station_pairs_df.arrival_time_x > station_pairs_df.departure_time_y)
                                )]

    def __add_halt_dep_filter_gt1day_layover(self, trains_df):
        train2_on_date_on_halt = self._if_train_runs_on_dayc_date(trains_df, 'train_number_y', 'day_count_y_y',
            trains_df['halt_arrival']) & (trains_df.departure_time_y_y > trains_df.arrival_time_y_x)
        train2_on_next_date_halt = self._if_train_runs_on_dayc_date(trains_df, 'train_number_y', 'day_count_y_y',
            trains_df['halt_arrival'] + pd.Timedelta(days=1))
        trains_df['halt_departure'] = np.where(train2_on_date_on_halt, trains_df['halt_arrival'],
                                                     trains_df['halt_arrival'] + pd.Timedelta(days=1))
        return trains_df[train2_on_date_on_halt | train2_on_next_date_halt].reset_index(drop=True)

    def _add_halt_details(self, trains_df, dt):
        trains_df['halt_arrival'] = pd.to_datetime(pd.Series([dt]*len(trains_df))) + \
            pd.to_timedelta(trains_df['day_count_y_x'] - trains_df['day_count_x_x'], unit='D')
        trains_df = self.__add_halt_dep_filter_gt1day_layover(trains_df)
        trains_df['halt_time_minutes'] = (
            (pd.to_datetime(trains_df['halt_departure']) +
                pd.to_timedelta(trains_df['departure_time_y_y'].astype(str))) -
            (pd.to_datetime(trains_df['halt_arrival']) +
                pd.to_timedelta(trains_df['arrival_time_y_x'].astype(str)))
        ).dt.total_seconds() / 60
        return trains_df

    def _filter_best_itinerary_for_train_pair(self, trains_df):
        trains_df.sort_values('halt_time_minutes', ascending=False, inplace=True)
        return trains_df.drop_duplicates(subset=['train_number_x', 'train_number_y']).reset_index(drop=True)

    def _add_more_travel_info(self, trains_df, dt):
        trains_df['reach_date'] = pd.to_datetime(trains_df['halt_departure']) + \
            pd.to_timedelta(trains_df['day_count_x_y'] - trains_df['day_count_y_y'], unit='D')
        trains_df['journey_time_minutes'] = (
            (pd.to_datetime(trains_df['reach_date']) +
                pd.to_timedelta(trains_df['arrival_time_x_y'].astype(str))) -
            (pd.to_datetime(pd.Series([dt]*len(trains_df))) +
                pd.to_timedelta(trains_df['departure_time_x_x'].astype(str)))
        ).dt.total_seconds() / 60
        return trains_df

    def format_time_columns(self, trains_df, dep_date):
        trains_df.reset_index(drop=True, inplace=True)
        trains_df['fromArrival']=pd.to_datetime(pd.Series([dep_date] * len(trains_df))) + \
                                 pd.to_timedelta(trains_df.arrival_time_x_x.astype(str), errors='coerce')
        trains_df['fromDeparture']=pd.to_datetime(pd.Series([dep_date] * len(trains_df))) + \
                                   pd.to_timedelta(trains_df.departure_time_x_x.astype(str))
        trains_df['haltArrival'] = pd.to_datetime(trains_df.halt_arrival) + \
                                   pd.to_timedelta(trains_df.arrival_time_y_x.astype(str))
        trains_df['haltDeparture']=pd.to_datetime(trains_df.halt_departure) + \
                                 pd.to_timedelta(trains_df.departure_time_y_y.astype(str))
        trains_df['toArrival']=pd.to_datetime(trains_df['reach_date']) + \
                               pd.to_timedelta(trains_df.arrival_time_y_x.astype(str))
        trains_df['toDeparture']=pd.to_datetime(trains_df.reach_date) + \
                                 pd.to_timedelta(trains_df.departure_time_y_x.astype(str), errors='coerce')
        return trains_df

    def format_direct(self, direct_trains, dep_date):
        direct_trains = self.format_time_columns(direct_trains, dep_date)
        direct_trains.rename({'station_code_x_x': 'fromStnCode', 'train_number_x': 'trainNumber',
                              'station_code_x_y': 'toStnCode'}, axis=1, inplace=True)
        return direct_trains[['fromStnCode', 'fromArrival', 'fromDeparture', 'trainNumber', 'toStnCode',
                              'toArrival', 'toDeparture', 'journey_time_minutes']].head(100)

    def format_indirect(self, indirect_trains, dep_date):
        indirect_trains = self.format_time_columns(indirect_trains, dep_date)
        indirect_trains.rename({'station_code_x_x': 'fromStnCode', 'train_number_x': 'trainNumber1', 'train_number_y':
            'trainNumber2', 'station_code_y': 'haltStation', 'station_code_x_y': 'toStnCode'}, axis=1, inplace=True)
        return indirect_trains[['fromStnCode', 'fromArrival', 'fromDeparture', 'trainNumber1', 'haltStation',
                                'haltArrival', 'trainNumber2', 'haltDeparture', 'halt_time_minutes', 'toStnCode',
                                'toArrival', 'toDeparture', 'journey_time_minutes']].head(100)

    def multi_train_itineraries(self, source_station, destination_station, dt):
        best_halting_on_date_source = self._trains_halting_on_nearby(source_station, dt)
        departing_halting_pairs = self._add_next_stops(best_halting_on_date_source)

        best_halting_dest = self._trains_halting_on_nearby(destination_station)
        halting_arriving_pairs = self._add_previous_stops(best_halting_dest)

        trains_df = departing_halting_pairs.merge(halting_arriving_pairs, 'inner', 'station_code_y')

        trains_df = self._add_halt_details(trains_df, dt)
        trains_df = self._filter_best_itinerary_for_train_pair(trains_df)
        trains_df = self._add_more_travel_info(trains_df, dt)
        trains_df.sort_values('journey_time_minutes', inplace=True)

        direct_trains = trains_df[trains_df.train_number_x == trains_df.train_number_y]
        indirect_trains = trains_df[trains_df.train_number_x != trains_df.train_number_y]
        return (self.format_direct(direct_trains, dt), self.format_indirect(indirect_trains, dt))

def main():
    source_station = 'MMCT'
    destination_station = 'NDLS'
    dt = date(2023, 11, 9)

    # Initialize the TrainsFinder with your database URL
    database_url = "postgresql://postgres:%s@localhost:5432/traintripper" % quote_plus(config("POSTGRES_PASSWORD"))
    planner = TrainsFinder(database_url)
    direct_trains, indirect_trains = planner.multi_train_itineraries(source_station, destination_station, dt)

if __name__ == "__main__":
    main()
