"""
Builds the dataframes-as-CSV "database" that SearchAlgorithm/algo_object_oriented.py reads (Data/database/*.csv) and the
distance_map precompute used by Combined/main.py, straight from the scraped Data/irctc/schedules.json.

This is the pandas replacement for populating_database.py + manipulation.sql + the first cells of
Processing/data_ready_for_model.ipynb, and reproduces their output format:
    python build_database.py
    python build_database.py --schedules other.json --out-dir some/dir --distance-map some/dir/distance_map
"""
import argparse
import json
import os
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
# city terminals no single train links within the nearby rule; hand-inserted into the original database
MANUAL_NEARBY = [('MMCT', 'CSMT', 10), ('MMCT', 'PNVL', 10), ('MMCT', 'BDTS', 5), ('KOAA', 'SHM', 8)]


def load_schedules(path):
    schedules = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                schedule = json.loads(line)
            except ValueError:
                continue
            if 'stationList' in schedule:
                schedules[schedule['trainNumber']] = schedule  # a re-scraped train overrides its older copy
    return list(schedules.values())


def build_trainschedules(schedules):
    rows = []
    for train in schedules:
        for stn in train['stationList']:
            rows.append({'station_code': stn['stationCode'], 'station_name': stn['stationName'],
                         'arrival_time': stn['arrivalTime'], 'departure_time': stn['departureTime'],
                         'route_number': int(stn['routeNumber']), 'distance': int(stn['distance']),
                         'day_count': int(stn['dayCount']), 'train_number': int(train['trainNumber']),
                         'halt_time': stn['haltTime']})
    df = pd.DataFrame(rows)
    for col in ['arrival_time', 'departure_time']:
        df[col] = pd.to_datetime(df[col], errors='coerce', format='%H:%M').dt.strftime('%H:%M:%S')

    # same as populating_database.haltTimeConverter: "--" -> -1, "HH:MM" -> HH*60+MM
    def halt_time_converter(string):
        if string == '--':
            return -1
        hours, mins = map(int, string.split(':'))
        return hours * 60 + mins
    df['halt_time_minutes'] = df.halt_time.apply(halt_time_converter)
    return df.drop(['halt_time'], axis=1)


def build_trains(schedules):
    df = pd.DataFrame([{'train_number': int(train['trainNumber']), 'train_name': train['trainName'],
                        'station_from': train['stationFrom'], 'station_to': train['stationTo'],
                        **{f'trainrunson{day.lower()}': train[f'trainRunsOn{day}'] == 'Y' for day in DAYS}}
                       for train in schedules])
    return df


def build_stations(trainschedules):
    return trainschedules[['station_code', 'station_name']].drop_duplicates(subset='station_code')


def build_nearbystations(schedules, stations):
    # Two stations are "nearby" when some train covers one from the other within 60 minutes and 30 km
    # on the same route. Kept in both directions, plus every station paired with itself at distance 0.
    pairs = []
    for sch in schedules:
        stations_list = sch['stationList']
        for i in range(len(stations_list) - 1):
            stn1 = stations_list[i]
            if stn1['departureTime'] == '--':
                continue
            dep_hour, dep_min = map(int, stn1['departureTime'].split(':'))
            for j in range(i + 1, len(stations_list)):
                stn2 = stations_list[j]
                if stn2['arrivalTime'] == '--':
                    continue
                if stn1['routeNumber'] != stn2['routeNumber']:
                    break
                arr_hour, arr_min = map(int, stn2['arrivalTime'].split(':'))
                diff_in_minutes = (int(stn2['dayCount']) - int(stn1['dayCount'])) * 1440 + \
                                  (arr_hour - dep_hour) * 60 + (arr_min - dep_min)
                distance = int(stn2['distance']) - int(stn1['distance'])
                if diff_in_minutes > 60 or distance > 30:
                    break
                pairs.append((stn1['stationCode'], stn2['stationCode'], distance))
                pairs.append((stn2['stationCode'], stn1['stationCode'], distance))
    for station1, station2, distance in MANUAL_NEARBY:
        pairs += [(station1, station2, distance), (station2, station1, distance)]
    df = pd.DataFrame(pairs, columns=['station1', 'station2', 'distance'])
    # manipulation.sql kept one arbitrary row per pair (distinct on without order by); keep the shortest
    df = df.sort_values('distance', kind='stable').drop_duplicates(subset=['station1', 'station2'])
    df = df[df.station1 != df.station2]
    selfs = pd.DataFrame({'station1': stations.station_code, 'station2': stations.station_code, 'distance': 0})
    return pd.concat([df, selfs]).sort_values(['station1', 'station2']).reset_index(drop=True)


def build_distance_map(trainschedules):
    # (train_number, station_code) -> (distance from origin, minutes from origin departure to arrival here),
    # as in Processing/data_ready_for_model.ipynb; a station visited twice keeps its last visit
    distance_map = {}
    for train_number, rows in trainschedules.groupby('train_number', sort=False):
        first = rows.iloc[0]
        departure = pd.to_timedelta(first.departure_time)
        for i, row in enumerate(rows.itertuples()):
            if i == 0:
                duration = 0.0
            else:
                reached = row.arrival_time if isinstance(row.arrival_time, str) else row.departure_time
                duration = (pd.to_timedelta(reached) + pd.to_timedelta(row.day_count - first.day_count, unit='D') -
                            departure).total_seconds() / 60
            distance_map[str((int(train_number), row.station_code))] = [int(row.distance), duration]
    return distance_map


def atomic_write(path, write):
    tmp = f'{path}.tmp'
    write(tmp)
    os.replace(tmp, path)


def write_json(obj, path):
    with open(path, 'w') as f:
        json.dump(obj, f)


def to_csv(df, path):
    df = df.copy()
    for col in df.columns[df.dtypes == bool]:
        df[col] = df[col].map({True: 'true', False: 'false'})
    atomic_write(path, lambda tmp: df.to_csv(tmp, index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--schedules', default=os.path.join(ROOT, 'Data', 'irctc', 'schedules.json'))
    parser.add_argument('--out-dir', default=os.path.join(ROOT, 'Data', 'database'))
    parser.add_argument('--distance-map', default=os.path.join(ROOT, 'Data', 'precomputes', 'distance_map'))
    args = parser.parse_args()

    schedules = load_schedules(args.schedules)
    trainschedules = build_trainschedules(schedules)
    stations = build_stations(trainschedules)
    nearbystations = build_nearbystations(schedules, stations)
    trains = build_trains(schedules)

    os.makedirs(args.out_dir, exist_ok=True)
    to_csv(trainschedules.drop(['station_name'], axis=1),
           os.path.join(args.out_dir, 'traintripper_trainschedules.csv'))
    to_csv(trains, os.path.join(args.out_dir, 'traintripper_trains.csv'))
    to_csv(stations, os.path.join(args.out_dir, 'traintripper_stations.csv'))
    to_csv(nearbystations, os.path.join(args.out_dir, 'traintripper_nearbystations.csv'))

    distance_map = build_distance_map(trainschedules)
    atomic_write(args.distance_map, lambda tmp: write_json(distance_map, tmp))

    print(f'{len(trains)} trains, {len(trainschedules)} schedule rows, {len(stations)} stations, '
          f'{len(nearbystations)} nearby pairs, {len(distance_map)} distance_map keys -> {args.out_dir}')


if __name__ == '__main__':
    main()
