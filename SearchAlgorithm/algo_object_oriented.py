from datetime import date, timedelta, datetime
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

class TrainTripPlanner:
    def __init__(self, database_url):
        self.db = TrainDatabase(database_url)

    def check_query_fairness(self, source_station, destination_station, dt):
        currDate = datetime.now().date()
        td = dt - currDate
        if td.days < 0 or td.days > 120:
            return False
        return self.db.check_station_exists(source_station) and self.db.check_station_exists(destination_station)

    def __if_train_dep_on_date(self, trains_df, train_num_name, date_series):
        schedules_df = self.db.fetch_train_info(trains_df[train_num_name].to_list())
        schedules_df.drop(['station_from', 'station_to'], axis=1, inplace=True)
        trains_with_days_df = trains_df.merge(schedules_df, 'left', left_on=train_num_name, right_on='train_number')
        day_series = date_series.apply(lambda dt: dt.strftime('%A')[:3].lower())
        return trains_with_days_df.apply(lambda row: row[f'trainrunson{day_series[row.name]}'], axis=1)

    def _if_train_runs_on_dayc_date(self, trains_df, train_num_name, day_count_name, dt):
        dt_conv = dt if isinstance(dt, pd.Series) else pd.Series([dt] * len(trains_df))
        req_dep_date_series = trains_df.apply(lambda row: dt_conv[row.name] - timedelta(row[day_count_name] - 1), axis=1)
        return self.__if_train_dep_on_date(trains_df, train_num_name, req_dep_date_series)

    def _filter_best_near_for_train(self, trains_df, station):
        nearby_station_dist = self.db.fetch_nearby_stations(station)
        trains_with_station_dist = trains_df.merge(nearby_station_dist, 'left', left_on='station_code', right_on='station2')
        trains_with_station_dist.sort_values(['distance_y'], inplace=True)
        trains_with_station_dist.drop_duplicates(subset='train_number', inplace=True)
        return trains_with_station_dist.drop(['station2', 'distance_y'], axis=1)

    def _get_departing_trains(self, source_station, dt):
        nearby_stations = self.db.fetch_nearby_stations(source_station).station2.to_list()
        departing_trains = self.db.trains_halting_on(nearby_stations)
        departing_on_date_trains = departing_trains[
            self._if_train_runs_on_dayc_date(departing_trains, 'train_number', 'day_count', dt)]
        return self._filter_best_near_for_train(departing_on_date_trains, source_station)

    def _get_arriving_trains(self, destination_station):
        nearby_stations = self.db.fetch_nearby_stations(destination_station).station2.to_list()
        arriving_trains = self.db.trains_halting_on(nearby_stations)
        return self._filter_best_near_for_train(arriving_trains, destination_station)

    def trains_on_date_running_nearby(self, source_station, destination_station, dt):
        if not self.check_query_fairness(source_station, destination_station, dt):
            return pd.DataFrame()
        best_departing_on_date_trains = self._get_departing_trains(source_station, dt)
        best_arriving_trains = self._get_arriving_trains(destination_station)
        common = best_departing_on_date_trains.merge(best_arriving_trains, 'inner', 'train_number')
        only_source_to_dest = common[common.distance_x_x < common.distance_x_y]
        return only_source_to_dest

    def trains_on_date_running_between(self, source_station, destination_station, dt):
        if not self.check_query_fairness(source_station, destination_station, dt):
            return pd.DataFrame()
        trains = self.trains_on_date_running_nearby(source_station, destination_station, dt)
        return trains[(trains.station_code_x == source_station) & (trains.station_code_y == destination_station)]

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

    def _filter_direct(self, trains_df, direct_train_numbers):
        indirect_trains = trains_df[~trains_df.train_number.isin(direct_train_numbers)]
        return indirect_trains.reset_index(drop=True)

    def __add_halt_dep_filter_gt1day_layover(self, indirect_trains):
        train2_on_date_on_halt=self._if_train_runs_on_dayc_date(indirect_trains, 'train_number_y', 'day_count_y_y',
                                                                indirect_trains['halt_arrival']) & (indirect_trains.departure_time_y_y > indirect_trains.arrival_time_y_x)
        train2_on_next_date_halt=self._if_train_runs_on_dayc_date(indirect_trains, 'train_number_y', 'day_count_y_y',
                                                                  indirect_trains['halt_arrival'] + timedelta(1))
        indirect_trains=indirect_trains[train2_on_date_on_halt | train2_on_next_date_halt]
        indirect_trains['halt_departure']=indirect_trains.apply(lambda row:
            row['halt_arrival'] if train2_on_date_on_halt[row.name] else row['halt_arrival'] + timedelta(1), axis=1)
        return indirect_trains

    def _add_halt_details(self, indirect_trains, dt):
        indirect_trains['halt_arrival'] = indirect_trains.apply(lambda row:
            dt + timedelta(row['day_count_y_x'] - row['day_count_x_x']), axis=1)
        indirect_trains = self.__add_halt_dep_filter_gt1day_layover(indirect_trains)
        indirect_trains['halt_time_minutes'] = indirect_trains.apply(lambda row:
            (datetime.combine(row['halt_departure'], row['departure_time_y_y']) -
             datetime.combine(row['halt_arrival'], row['arrival_time_y_x'])).total_seconds() / 60, axis=1)
        return indirect_trains

    def _filter_best_itinerary_for_train_pair(self, indirect_trains):
        indirect_trains.sort_values('halt_time_minutes', ascending=False, inplace=True)
        return indirect_trains.drop_duplicates(subset=['train_number_x', 'train_number_y'])

    def _add_more_travel_info(self, indirect_trains, dt):
        indirect_trains['reach_date']=indirect_trains.apply(lambda row:
            row['halt_departure'] + timedelta(row['day_count_x_y'] - row['day_count_y_y']), axis=1)
        indirect_trains['journey_time_minutes'] = indirect_trains.apply(lambda row:
            (datetime.combine(row['reach_date'], row['arrival_time_x_y']) -
             datetime.combine(dt, row['departure_time_x_x'])).total_seconds() / 60, axis=1)
        return indirect_trains

    def multi_train_itineraries(self, source_station, destination_station, dt):
        if not self.check_query_fairness(source_station, destination_station, dt):
            return pd.DataFrame()
        direct_train_numbers = self.trains_on_date_running_nearby(source_station, destination_station, dt)\
                                .train_number.to_list()

        best_departing_on_date_trains = self._get_departing_trains(source_station, dt)
        departing_on_date_indirect = self._filter_direct(best_departing_on_date_trains, direct_train_numbers)
        departing_halting_pairs = self._add_next_stops(departing_on_date_indirect)

        best_arriving_trains = self._get_arriving_trains(destination_station)
        arriving_indirect = self._filter_direct(best_arriving_trains, direct_train_numbers)
        halting_arriving_pairs = self._add_previous_stops(arriving_indirect)

        indirect_trains = departing_halting_pairs.merge(halting_arriving_pairs, 'inner', 'station_code_y')

        indirect_trains = self._add_halt_details(indirect_trains, dt)
        indirect_trains = self._filter_best_itinerary_for_train_pair(indirect_trains)
        indirect_trains = self._add_more_travel_info(indirect_trains, dt)
        return indirect_trains.sort_values('journey_time_minutes').head(100)

def main():
    source_station = 'MMCT'
    destination_station = 'NDLS'
    dt = date(2023, 10, 26)

    # Initialize the TrainTripPlanner with your database URL
    database_url = "postgresql://postgres:%s@localhost:5432/traintripper" % quote_plus(config("POSTGRES_PASSWORD"))
    planner = TrainTripPlanner(database_url)

    # Use the planner to find train itineraries
    df = planner.trains_on_date_running_nearby(source_station, destination_station, dt)
    df1 = planner.trains_on_date_running_between(source_station, destination_station, dt)
    df2 = planner.multi_train_itineraries(source_station, destination_station, dt)

if __name__ == "__main__":
    main()
