import argparse
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

BASE_PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_PATH))

from Collection.Scrapping.utils import (BulkAvailabilityClient, IRCTCClient, TrainListClient, TrainPriceAndAvailClient,
                                        WaitlistPredictionClient)
from SearchAlgorithm.algo_object_oriented import TrainsFinder

DEFAULT_CLASSES = ['SL', '3E', '3A', '2A', '1A', '2S', 'CC', 'EC']
STATUS_RANK = {'AVL': 0, 'RAC': 1, 'WL': 2, 'NA': 3}
# answers from the prediction endpoint that mean the logged-in session is gone, so there's no point asking again
SESSION_ERRORS = ('status 401', 'status 403', 'login', 'session', 'verify your mobile', 'unauthori', 'token')
# sort key -> (columns, ascending, tie-break keys). With several keys ties on one go to the next, and what is still
# tied goes to the first key's tie-breaks. 'probability' is IRCTC's chance (seats now count as 100%), then status:
# confirmed > RAC > WL, and without a percentage the lower waitlist number. 'seats' is the tightest leg's seats on
# journeys confirmable now; 'wait' is the halt between the two trains (none on a direct train)
SORTS = {
    'departure': (['departure'], [True], ['arrival']),
    'arrival': (['arrival'], [True], ['duration']),
    'duration': (['duration'], [True], ['departure']),
    'fare': (['totalFare'], [True], ['departure']),
    'wait': (['wait'], [True], ['arrival']),
    'seats': (['seats'], [False], ['arrival']),
    'probability': (['probability', 'worstRank', 'waitlistNumber'], [False, True, True], ['seats', 'arrival']),
}


def parse_status(status):
    """'AVAILABLE-0141' -> ('AVL', 141), 'GNWL44/WL31' -> ('WL', 31), 'RAC12/RAC5' -> ('RAC', 5), else ('NA', None).
    IRCTC reports '<booking status>/<current status>'; the part after the slash is what you'd get now."""
    status = status.upper()
    current = status.split('/')[-1].strip()
    if 'REGRET' in status:
        return 'NA', None
    for kind, pattern in (('AVL', r'(?:AVAILABLE|CURR_AVBL)-?0*(\d+)'), ('RAC', r'RAC\s*0*(\d+)'), ('WL', r'WL\s*0*(\d+)')):
        match = re.search(pattern, current)
        if match:
            return kind, int(match.group(1))
    return 'NA', None


def leg_chance(result):
    """IRCTC's confirmation probability when it gave one; seats available now count as certain"""
    if result['probability'] is not None:
        return result['probability']
    return 1.0 if result['kind'] == 'AVL' else None


class AvailabilityChecker:
    """Live per-class availability and fare for train legs (train_number, from, to, boarding date).
    A leg is only queried for the classes IRCTC's live train list says it has, and legs whose train is
    missing from that list (not running that day / schedule changed since our data was scraped) are dropped.
    Legs are asked about in bulk, BulkAvailabilityClient.MAX_LEGS per request, falling back to one request per
    class for whatever a bulk answer leaves out."""

    def __init__(self, irctc_client: IRCTCClient, quota='GN', workers=3, predictor: WaitlistPredictionClient = None):
        self.train_list_client = TrainListClient(irctc_client)
        self.fare_client = TrainPriceAndAvailClient(irctc_client)
        self.bulk_client = BulkAvailabilityClient(irctc_client)
        self.predictor = predictor
        self.quota = quota
        self.workers = workers
        self._live_lists = {}
        self._class_results = {}
        self._session_error = None
        self._prediction_failures = []
        self._bulk_failed = False

    def _parallel(self, function, jobs, desc):
        """function(job) for every job on the worker threads, results in job order. The progress bar is drawn here
        on the main thread, so it never holds up a request; the pace is set by IRCTCClient's throttle"""
        with ThreadPoolExecutor(self.workers) as executor:
            futures = [executor.submit(function, job) for job in jobs]
            with tqdm(total=len(jobs), desc=desc, unit='req', disable=len(jobs) < 2) as bar:
                for _ in as_completed(futures):
                    bar.update()
            return [future.result() for future in futures]

    def _fetch_live_list(self, pair):
        source_station, destination_station, dt = pair
        response = self.train_list_client.request_wrapper(source_station, destination_station, dt.strftime('%Y%m%d'),
                                                          self.quota)
        if 'errorMessage' in response and 'trainBtwnStnsList' not in response:
            # IRCTC answers "no trains" with an errorMessage too; only transport failures leave us without a verdict
            return None if str(response['errorMessage']).startswith('Request to IRCTC failed') else {}
        return {int(train['trainNumber']): train for train in response.get('trainBtwnStnsList', [])}

    def live_trains(self, pairs):
        """{(from, to, date): {train_number: train_json} or None if IRCTC couldn't be reached}"""
        missing = [pair for pair in set(pairs) if pair not in self._live_lists]
        self._live_lists.update(zip(missing, self._parallel(self._fetch_live_list, missing, 'Train lists')))
        return {pair: self._live_lists[pair] for pair in pairs}

    def _fetch_class(self, job):
        (train_number, source_station, destination_station, dt), class_code = job
        response = self.fare_client.get(f'{train_number:05d}', dt.strftime('%Y%m%d'), source_station,
                                        destination_station, class_code, self.quota)
        return self._class_result(class_code, response, dt)

    def _fetch_bulk(self, batch):
        """{(leg, class): result} for a batch of (leg, classes) jobs"""
        legs = [(f'{train_number:05d}', source_station, destination_station, dt.strftime('%Y%m%d'), classes)
                for (train_number, source_station, destination_station, dt), classes in batch]
        answers = [None] * len(legs)
        if not self._bulk_failed:
            response = self.bulk_client.request_wrapper(legs, self.quota)
            if 'trainBtwnStnsList' in response:
                answers = self.bulk_client.filter_data(response, legs)
            else:
                self._bulk_failed = True  # don't keep retrying it: one request per class for the rest
        results = {}
        for (leg, classes), answer in zip(batch, answers):
            for class_code in classes:
                if answer and class_code in answer:
                    results[(leg, class_code)] = self._class_result(class_code, answer[class_code], leg[3])
                else:
                    results[(leg, class_code)] = self._fetch_class((leg, class_code))
        return results

    @staticmethod
    def _class_result(class_code, response, dt):
        if 'errorMessage' in response:
            message = re.sub(r'\s*-?\s*\(\d+\)$', '', response['errorMessage']).strip()
            if message.startswith('Class does not exist'):
                return None
            # e.g. "SRC DESTINATION PAIR OF SAME SUBURBAN AREA IS INVALID": the leg exists but can't be booked
            return {'classCode': class_code, 'status': message.upper()[:60], 'kind': 'NA', 'count': None,
                    'fare': None, 'probability': None}
        # IRCTC writes 5 Nov as '5-11-2026', not '05-11-2026'
        day = next((day for day in response['availability']
                    if datetime.strptime(day['date'], '%d-%m-%Y').date() == dt), None)
        status = day['status'] if day else f'NO STATUS FOR {dt:%d %b}'.upper()
        if 'CLASS NOT EXIST' in status.upper():
            return None
        kind, count = parse_status(status)
        return {'classCode': class_code, 'status': status, 'kind': kind, 'count': count,
                'fare': int(float(response['totalFare'])), 'probability': day.get('probability') if day else None,
                'wlType': day.get('wlType') if day else None}

    def _predict(self, job):
        (train_number, source_station, destination_station, dt), result = job
        if self._session_error:
            return None
        train = (self._live_lists.get((source_station, destination_station, dt)) or {}).get(train_number, {})
        response = self.predictor.get(f'{train_number:05d}', dt.strftime('%Y%m%d'), source_station,
                                      destination_station, result['classCode'], result['status'],
                                      result.get('wlType'), train.get('trainsiteId'), self.quota)
        if 'errorMessage' in response:
            if any(error in response['errorMessage'].lower() for error in SESSION_ERRORS):
                self._session_error = self._session_error or response['errorMessage']
            else:
                self._prediction_failures.append(response['errorMessage'])
            return None
        return response['probability']

    def predict(self, jobs, skipped=0):
        """IRCTC's confirmation chance, asked like the website's per-class 'check chance', for (leg, class result)
        jobs; each result gets its 'probability'"""
        if not self.predictor:
            return
        if not jobs:
            print("No waitlisted class worth asking IRCTC's confirmation chance for: every journey either has seats on "
                  "every leg or a leg that can't be booked (REGRET / NOT AVAILABLE)")
            return
        self._prediction_failures = []
        desc = 'Confirmation chance (logged in)' + (f', best {len(jobs)} of {len(jobs) + skipped}' if skipped else '')
        probabilities = self._parallel(self._predict, jobs, desc)
        for (_, result), probability in zip(jobs, probabilities):
            result['probability'] = probability
        if self._session_error:
            print(f"IRCTC stopped giving confirmation chances ({self._session_error}); the logged-in session has "
                  f"probably expired, so copy a fresh request for --irctc-session")
        elif self._prediction_failures:
            print(f"No confirmation chance for {len(self._prediction_failures)} of them, e.g. "
                  f"{self._prediction_failures[0]!r}")

    def check(self, legs, class_preference):
        """{leg: [class result, ...] in class_preference order} for every leg that runs"""
        legs = set(legs)
        live = self.live_trains({(source, destination, dt) for _, source, destination, dt in legs})
        running = {}
        for leg in legs:
            train_number, source_station, destination_station, dt = leg
            trains = live[(source_station, destination_station, dt)]
            train = (trains or {}).get(train_number)
            if trains is None:
                running[leg] = class_preference
            # IRCTC also lists trains ending at nearby stations, so a different station code means our schedule is stale
            elif train and (train['fromStnCode'], train['toStnCode']) == (source_station, destination_station):
                running[leg] = [cc for cc in class_preference if cc in train['avlClasses']]

        jobs = [(leg, [cc for cc in classes if (leg, cc) not in self._class_results]) for leg, classes in running.items()]
        jobs = [(leg, classes) for leg, classes in jobs if classes]
        size = BulkAvailabilityClient.MAX_LEGS
        batches = [jobs[start:start + size] for start in range(0, len(jobs), size)]
        for batch_results in self._parallel(self._fetch_bulk, batches,
                                            f'Availability ({len(jobs)} legs, {size} per request)'):
            self._class_results.update(batch_results)
        return {leg: [self._class_results[(leg, cc)] for cc in classes if self._class_results[(leg, cc)]]
                for leg, classes in running.items()}


def best_class(class_results):
    """first confirmed class in preference order, else the best IRCTC probability, else the least-bad status"""
    confirmed = [result for result in class_results if result['kind'] == 'AVL']
    if confirmed:
        return confirmed[0]
    return min(class_results, key=lambda result: (-(leg_chance(result) if leg_chance(result) is not None else -1),
                                                  STATUS_RANK[result['kind']], result['count'] or 0))


def short_status(status):
    """IRCTC's status, with its long can't-book-this-pair messages cut down to fit a table column"""
    for words, short in (('SUBURBAN', 'SUBURBAN PAIR'), ('NOT ALLOWED', 'NOT ALLOWED')):
        if words in status and len(status) > 20:
            return short
    return status if len(status) <= 20 else status[:18] + '..'


def summarize(class_results, short=False):
    return ' | '.join(f"{r['classCode']} {short_status(r['status']) if short else r['status']}" +
                      (f" ({r['probability']:.0%})" if r['probability'] is not None else '') +
                      (f" Rs{r['fare']}" if r['fare'] else '') for r in class_results)


def journey_status(bests):
    """(worst leg rank, tie-break count, chance) for a journey whose legs got these best classes"""
    worst = max(STATUS_RANK[best['kind']] for best in bests)
    counts = [best['count'] for best in bests if STATUS_RANK[best['kind']] == worst and best['count'] is not None]
    if worst == STATUS_RANK['AVL']:
        count = -min(counts)  # fewest seats on any leg is the bottleneck; more seats sorts first
    else:
        count = max(counts) if counts else np.nan
    chances = [leg_chance(best) for best in bests]
    chance = np.prod(chances) if all(c is not None for c in chances) else np.nan
    return worst, count, chance


def time_window(text):
    """'06:00-12:00' -> (360, 720, False): clock times on any day; the end may be earlier than the start to wrap past
    midnight. A '+N' on either end counts days after the journey date instead: '23:59-10:00+1' -> (1439, 2040, True)"""
    ends = []
    for part in text.split('-'):
        match = re.fullmatch(r'\s*(\d{1,2}:\d{2})(?:\s*\+\s*(\d+))?\s*', part)
        try:
            clock = time.fromisoformat(match.group(1).zfill(5))
        except (AttributeError, ValueError):
            raise argparse.ArgumentTypeError(f"expected HH:MM-HH:MM, optionally +N days on either end, got {text!r}")
        ends.append((clock.hour * 60 + clock.minute, match.group(2)))
    if len(ends) != 2:
        raise argparse.ArgumentTypeError(f"expected HH:MM-HH:MM, optionally +N days on either end, got {text!r}")
    by_day = any(days is not None for _, days in ends)
    (start, start_days), (end, end_days) = ends
    if by_day:
        start, end = start + 1440 * int(start_days or 0), end + 1440 * int(end_days or 0)
        if end < start:
            raise argparse.ArgumentTypeError(f"{text!r} ends before it starts")
    return start, end, by_day


def departure_days(depart_window):
    """days after the journey date that a departure window reaches into: only a +N window goes past the first"""
    if not depart_window or not depart_window[2]:
        return [0]
    start, end, _ = depart_window
    return list(range(max(start, 0) // 1440, end // 1440 + 1))


def in_window(timestamps, window, dt=None):
    """clock-time match like IRCTC's departure/arrival filters, whatever the day; or, for a window with +N days,
    the exact span counted from midnight of the journey date dt"""
    start, end, by_day = window
    if by_day:
        return ((timestamps - pd.Timestamp(dt)) / pd.Timedelta(minutes=1)).between(start, end)
    minutes = timestamps.dt.hour * 60 + timestamps.dt.minute
    if start <= end:
        return minutes.between(start, end)
    return (minutes >= start) | (minutes <= end)


class TripPlannerWithAvailability:
    def __init__(self, base_path=BASE_PATH, class_preference=DEFAULT_CLASSES, quota='GN', irctc_client=None,
                 workers=3, predictor=None):
        self.train_finder = TrainsFinder(base_path)
        self.checker = AvailabilityChecker(irctc_client or IRCTCClient(), quota, workers, predictor)
        self.class_preference = list(class_preference)
        self.train_names = self.train_finder.db.trains.set_index('train_number').train_name.to_dict()
        schedules = self.train_finder.db.trainschedules
        # km from the train's origin at each of its stations, and minutes from its departure to arriving there
        self.route_km = schedules.groupby(['train_number', 'station_code']).distance.first()
        self.route_minutes = (pd.to_timedelta(schedules.arrival_time).dt.total_seconds() / 60 +
                              (schedules.day_count - 1) * 1440).groupby([schedules.train_number,
                                                                         schedules.station_code]).first()

    def _add_other_exits(self, journeys, dest_station):
        """the search ends every journey at the station nearest the destination, but the last train may call at
        other stations near it first (Borivali before Mumbai Central), arriving earlier: add those as variants"""
        near = self.train_finder.db.fetch_nearby_stations(dest_station).groupby('station2').distance.min()
        is_split = (journeys.type == 'split').to_numpy()
        last_train = pd.Series(np.where(is_split, journeys.train2, journeys.train1), index=journeys.index)
        boarding = journeys.haltStation.where(is_split, journeys.fromStnCode)

        def at(table, stations):
            index = pd.MultiIndex.from_arrays([last_train.fillna(-1).astype('int64').to_numpy(),
                                               stations.fillna('').to_numpy()])
            return table.reindex(index).to_numpy(dtype=float)
        rows = pd.DataFrame({'row': journeys.index, 'train': last_train.fillna(-1).astype('int64').to_numpy(),
                             'boardKm': at(self.route_km, boarding), 'exitKm': at(self.route_km, journeys.toStnCode),
                             'exitMinutes': at(self.route_minutes, journeys.toStnCode),
                             'exit': journeys.toStnCode.to_numpy()})
        stops = pd.DataFrame({'km': self.route_km, 'minutes': self.route_minutes}).reset_index()
        stops = stops[stops.station_code.isin(near.index)]
        options = rows.merge(stops, left_on='train', right_on='train_number')
        options = options[(options.km > options.boardKm) & (options.station_code != options.exit) &
                          options.minutes.notna() & options.exitMinutes.notna()]

        variants = journeys.loc[options.row].reset_index(drop=True)
        shift = pd.to_timedelta((options.minutes - options.exitMinutes).to_numpy(), unit='min')
        variants['toStnCode'] = options.station_code.to_numpy()
        variants['arrival'] += shift
        variants['duration'] += shift
        variants['distance'] += (options.km - options.exitKm).to_numpy()
        journeys = pd.concat([journeys, variants], ignore_index=True)
        journeys['exitKmToDestination'] = near.reindex(journeys.toStnCode).to_numpy()
        return journeys

    @staticmethod
    def _nearest_exit(journeys):
        """one exit per journey: of those left after the filters, the station nearest the destination"""
        key = ['type', 'fromStnCode', 'departure', 'train1', 'haltStation', 'train2']
        journeys = journeys.sort_values(['exitKmToDestination', 'arrival'], kind='stable', na_position='last')
        return journeys.drop_duplicates(key).sort_index().drop(columns='exitKmToDestination')

    def _leg_km(self, trains, from_stations, to_stations):
        def km_at(stations):
            index = pd.MultiIndex.from_arrays([trains.fillna(-1).astype('int64').to_numpy(),
                                               stations.fillna('').to_numpy()])
            return self.route_km.reindex(index).to_numpy(dtype=float)
        return km_at(to_stations) - km_at(from_stations)

    def _add_distance(self, journeys):
        """travelled km over both legs (direct trains IRCTC listed but our data lacks keep IRCTC's figure)"""
        is_split = (journeys.type == 'split').to_numpy()
        first = self._leg_km(journeys.train1, journeys.fromStnCode, journeys.haltStation.where(is_split,
                                                                                              journeys.toStnCode))
        second = np.where(is_split, self._leg_km(journeys.train2, journeys.haltStation, journeys.toStnCode), 0)
        journeys['distance'] = pd.Series(first + second, index=journeys.index).fillna(journeys.distance)
        return journeys

    def _add_live_direct(self, direct_trains, source_station, dest_station, dt):
        """IRCTC's own list for the exact station pair catches trains that are newer than our schedule data"""
        live = self.checker.live_trains([(source_station, dest_station, dt)])[(source_station, dest_station, dt)]
        known = set(direct_trains.trainNumber)
        rows = []
        for train_number, train in (live or {}).items():
            if train_number in known:
                continue
            self.train_names.setdefault(train_number, train['trainName'])
            departure = datetime.combine(dt, time.fromisoformat(train['departureTime']))
            hours, minutes = map(int, train['duration'].split(':'))
            duration = timedelta(hours=hours, minutes=minutes)
            rows.append({'fromStnCode': train['fromStnCode'], 'fromDeparture': departure, 'trainNumber': train_number,
                         'toStnCode': train['toStnCode'], 'toArrival': departure + duration, 'journey_time': duration,
                         'distance': pd.to_numeric(train.get('distance'), errors='coerce')})
        frames = [frame for frame in (direct_trains, pd.DataFrame(rows)) if len(frame)]
        return pd.concat(frames, ignore_index=True) if len(frames) > 1 else (frames or [direct_trains])[0]

    @staticmethod
    def _unify(direct_trains, indirect_trains):
        """one row per journey whether it is one train or two, so they can be filtered and sorted together"""
        direct = pd.DataFrame({
            'type': 'direct', 'fromStnCode': direct_trains.fromStnCode, 'departure': direct_trains.fromDeparture,
            'toStnCode': direct_trains.toStnCode, 'arrival': direct_trains.toArrival,
            'duration': direct_trains.journey_time, 'train1': direct_trains.trainNumber,
            'distance': direct_trains.get('distance', pd.Series(np.nan, index=direct_trains.index)).astype(float)})
        split = pd.DataFrame({
            'type': 'split', 'fromStnCode': indirect_trains.fromStnCode, 'departure': indirect_trains.fromDeparture,
            'toStnCode': indirect_trains.toStnCode, 'arrival': indirect_trains.toArrival,
            'duration': indirect_trains.journey_time, 'train1': indirect_trains.trainNumber1,
            'haltStation': indirect_trains.haltStation, 'haltArrival': indirect_trains.haltArrival,
            'haltDeparture': indirect_trains.haltDeparture, 'halt_time': indirect_trains.halt_time,
            'train2': indirect_trains.trainNumber2, 'distance': np.nan})
        for column in ('haltStation', 'haltArrival', 'haltDeparture', 'halt_time', 'train2'):
            direct[column] = pd.Series(dtype=split[column].dtype, index=direct.index)
        journeys = pd.concat([frame for frame in (direct, split) if len(frame)] or [split], ignore_index=True)
        return journeys.astype({'train1': 'Int64', 'train2': 'Int64'})

    @staticmethod
    def filter_schedule(journeys, kind='all', depart_window=None, arrive_window=None, from_stations=None,
                        to_stations=None, max_duration=None, dt=None):
        mask = pd.Series(True, index=journeys.index)
        if kind != 'all':
            mask &= journeys.type == kind
        if depart_window:
            mask &= in_window(journeys.departure, depart_window, dt)
        if arrive_window:
            mask &= in_window(journeys.arrival, arrive_window, dt)
        if from_stations:
            mask &= journeys.fromStnCode.isin(from_stations)
        if to_stations:
            mask &= journeys.toStnCode.isin(to_stations)
        if max_duration is not None:
            mask &= journeys.duration <= max_duration
        return journeys[mask]

    @staticmethod
    def _shortlist_split(journeys, min_halt, max_halt, max_detour, max_split):
        """there are thousands of halt-station combinations; keep realistic layovers, no detours (travelling more
        than max_detour times the shortest route's km; slow trains and long waits are fine) and the fastest few.
        Each source station searched gets its own shortest route and its own max_split: BRC is nearer Mumbai than ADI.
        An earlier station's boarding of a later station's journey (_board_earlier) stays with that journey"""
        kept = [journeys[journeys.type == 'direct']]
        split = journeys[(journeys.type == 'split') & (journeys.halt_time >= min_halt) &
                         (journeys.halt_time <= max_halt)]
        for source, found in journeys[~journeys.boardedEarlier].groupby('searchFrom', sort=False):
            options = split[(split.searchFrom == source) & ~split.boardedEarlier]
            shortest = found.distance.min()
            if pd.notna(shortest):
                options = options[options.distance.isna() | (options.distance <= max_detour * shortest)]
            options = options.sort_values('duration', kind='stable')
            if len(options) > max_split:
                print(f"Checking the fastest {max_split} of {len(options)} split journeys from {source} (--max-split "
                      f"to check more)")
                options = options.head(max_split)
            kept.append(options)
        same_trains = ['train1', 'haltStation', 'train2', 'toStnCode', 'arrival']
        chosen = [frame[same_trains] for frame in kept[1:] if len(frame)]
        twins = split[split.boardedEarlier]
        if chosen and len(twins):
            chosen = pd.MultiIndex.from_frame(pd.concat(chosen))
            kept.append(twins[pd.MultiIndex.from_frame(twins[same_trains]).isin(chosen)])
        return pd.concat([frame for frame in kept if len(frame)] or kept[:1], ignore_index=True)

    def _board_earlier(self, journeys, sources):
        """the search keeps one halt per pair of trains, so from ADI 19483 then 12952 changes at BRC (12952 waitlisted
        there) while from BRC the same pair changes at ST (seats). Give each later-listed station's split journeys an
        earlier-listed station's boarding of the same run of the first train, so _prefer_first_source can compare
        the same journey from both"""
        journeys = journeys.assign(boardedEarlier=False)
        if len(sources) < 2:
            return journeys
        split = journeys[journeys.type == 'split'].assign(
            rank=journeys.searchFrom.map({source: number for number, source in enumerate(sources)}))
        pairs = split.merge(split, on=['train1', 'train2', 'toStnCode', 'arrival'], suffixes=('', 'Earlier'))
        pairs = pairs[(pairs.rankEarlier < pairs['rank']) & (pairs.haltStation != pairs.haltStationEarlier) &
                      (pairs.departureEarlier < pairs.departure) &
                      (pairs.departure - pairs.departureEarlier < pd.Timedelta(days=1))]
        variants = pairs[journeys.columns].copy()
        for column in ('fromStnCode', 'departure', 'searchFrom'):
            variants[column] = pairs[f'{column}Earlier'].to_numpy()
        variants['duration'] = variants.arrival - variants.departure
        variants['boardedEarlier'] = True
        return pd.concat([journeys, self._add_distance(variants.reset_index(drop=True))], ignore_index=True)

    @staticmethod
    def _prefer_first_source(journeys, legs, availability, sources):
        """the same trains can often be boarded in more than one searched zone (12655 calls at ADI, then BRC). Where
        an earlier-listed station's first train has seats, drop the later stations' copies of that journey; while it
        is waitlisted keep them, as each boarding station has its own waitlist and chance"""
        if len(sources) < 2 or not len(journeys):
            return journeys, legs
        has_seats = pd.Series([any(result['kind'] == 'AVL' for result in availability[pair[0]]) for pair in legs],
                              index=journeys.index)
        rank = journeys.searchFrom.map({source: number for number, source in enumerate(sources)})
        same_trains = [journeys[column] for column in
                       ('type', 'train1', 'haltStation', 'train2', 'toStnCode', 'arrival')]
        first_with_seats = rank.where(has_seats).groupby(same_trains, dropna=False).transform('min')
        drop = rank > first_with_seats
        if drop.any():
            print(f"Left out {int(drop.sum())} journeys from {', '.join(journeys.searchFrom[drop].unique())}: the same "
                  f"trains have seats boarding at a station listed before it")
        return journeys[~drop].reset_index(drop=True), [pair for pair, dropped in zip(legs, drop) if not dropped]

    @staticmethod
    def _legs(journeys):
        """(leg1, leg2) per journey with leg = (train, from, to, boarding date); leg2 is None for direct trains"""
        is_split = journeys.type == 'split'
        leg1 = zip(journeys.train1, journeys.fromStnCode, journeys.haltStation.where(is_split, journeys.toStnCode),
                   journeys.departure.dt.date)
        leg2 = zip(journeys.train2, journeys.haltStation, journeys.toStnCode, journeys.haltDeparture.dt.date)
        return [(first, second if split else None) for first, second, split in zip(leg1, leg2, is_split)]

    def _annotate(self, journeys, legs, availability):
        journeys = journeys.copy()
        bests = []
        for number in (0, 1):
            results = [availability[pair[number]] if pair[number] else None for pair in legs]
            best = [best_class(result) if result else None for result in results]
            bests.append(best)
            prefix = f'leg{number + 1}'
            journeys[f'{prefix}Name'] = [self.train_names.get(pair[number][0]) if pair[number] else None
                                         for pair in legs]
            journeys[f'{prefix}Class'] = [b['classCode'] if b else None for b in best]
            journeys[f'{prefix}Status'] = [b['status'] if b else None for b in best]
            journeys[f'{prefix}Fare'] = pd.array([b['fare'] if b else None for b in best], dtype='Int64')
            journeys[f'{prefix}Probability'] = [b['probability'] if b and b['probability'] is not None else np.nan
                                                for b in best]
            journeys[f'{prefix}Availability'] = [summarize(result) if result else None for result in results]
            journeys[f'{prefix}Others'] = [summarize([r for r in result if r is not b], short=True) if result else None
                                           for result, b in zip(results, best)]

        statuses = [journey_status([b for b in pair if b]) for pair in zip(*bests)]
        journeys['totalFare'] = journeys.leg1Fare + journeys.leg2Fare.where(journeys.type == 'split', 0)
        journeys['worstRank'] = [status[0] for status in statuses]
        journeys['worstCount'] = [status[1] for status in statuses]
        journeys['probability'] = [status[2] for status in statuses]
        journeys['confirmed'] = journeys.worstRank == STATUS_RANK['AVL']
        return journeys

    def _predict_best_first(self, journeys, legs, availability, max_predictions):
        """IRCTC's chance for the waitlisted classes of the most promising journeys first, leg by leg until
        max_predictions: journeys with one waitlisted leg (the other confirmed, or direct) before two, then by
        waitlist number and speed. A leg with seats in any wanted class is certain anyway, and a journey with an
        unbookable leg (REGRET etc.) can't be helped by a prediction"""
        ranked = self._annotate(journeys, legs, availability)
        ranked['waitlistedLegs'] = [sum(1 for leg in pair if leg and not any(result['kind'] == 'AVL'
                                                                             for result in availability[leg]))
                                    for pair in legs]
        ranked = ranked[ranked.worstRank < STATUS_RANK['NA']]
        order = ranked.sort_values(['waitlistedLegs', 'worstRank', 'worstCount', 'duration'], kind='stable').index
        jobs, skipped, seen = [], 0, set()
        for index in order:
            for leg in legs[index]:
                if leg is None or leg in seen or any(result['kind'] == 'AVL' for result in availability[leg]):
                    continue
                seen.add(leg)
                waitlisted = [(leg, result) for result in availability[leg]
                              if result['kind'] in ('WL', 'RAC') and result['probability'] is None]
                if skipped or len(jobs) + len(waitlisted) > max_predictions:
                    skipped += len(waitlisted)
                else:
                    jobs += waitlisted
        self.checker.predict(jobs, skipped)

    def query(self, source_station: str, dest_station: str, dt: date, kind='all', depart_window=None,
              arrive_window=None, from_stations=None, to_stations=None, min_halt=timedelta(minutes=30),
              max_halt=timedelta(hours=6), max_journey=None, max_split=200, max_predictions=80, max_detour=1.25):
        """direct and split journeys matching the schedule filters, with live availability of every leg.
        source_station may be several ('ADI,BRC' or a list): each is searched with its nearby stations"""
        sources = source_station.split(',') if isinstance(source_station, str) else list(source_station)
        found = []
        for source in sources:
            # the search finds trains leaving on one date, so a window like 21:00-10:00+1 needs the next day's too
            for day in departure_days(depart_window):
                leaving = dt + timedelta(days=day)
                direct_trains, indirect_trains = self.train_finder.multi_train_itineraries(source, dest_station,
                                                                                           leaving)
                if kind != 'split':
                    direct_trains = self._add_live_direct(direct_trains, source, dest_station, leaving)
                found.append(self._add_distance(self._unify(direct_trains, indirect_trains)).assign(searchFrom=source))
        # nearby-station zones can overlap (ADI's has MAN, so ADI,MAN finds MAN's trains twice), and _board_earlier
        # can rebuild a journey the earlier station's search already has: keep each journey once
        journeys = self._board_earlier(pd.concat([frame for frame in found if len(frame)] or found[:1],
                                                 ignore_index=True), sources)
        journeys = journeys.drop_duplicates(['type', 'fromStnCode', 'departure', 'train1', 'haltStation', 'train2',
                                             'toStnCode'], ignore_index=True)
        journeys = self._add_other_exits(journeys, dest_station)
        journeys = self._nearest_exit(self.filter_schedule(journeys, kind, depart_window, arrive_window,
                                                           from_stations, to_stations, max_journey, dt))
        journeys = self._shortlist_split(journeys, min_halt, max_halt, max_detour, max_split)

        legs = self._legs(journeys)
        availability = self.checker.check([leg for pair in legs for leg in pair if leg], self.class_preference)
        # drop journeys with a leg that doesn't run (stale schedule) or has none of the requested classes
        keep = [all(leg is None or availability.get(leg) for leg in pair) for pair in legs]
        journeys = journeys[np.array(keep, dtype=bool)].reset_index(drop=True)
        legs = [pair for pair, kept in zip(legs, keep) if kept]
        journeys, legs = self._prefer_first_source(journeys, legs, availability, sources)
        if self.checker.predictor:
            self._predict_best_first(journeys, legs, availability, max_predictions)
        return self._annotate(journeys, legs, availability)


def filter_availability(journeys, available_only=False, min_probability=None):
    if available_only:
        journeys = journeys[journeys.confirmed]
    if min_probability is not None:
        journeys = journeys[journeys.probability >= min_probability / 100]
    return journeys


def sort_keys(text):
    """'probability, arrival,wait' -> ['probability', 'arrival', 'wait']; also the --sort argument type"""
    keys = [key.strip().lower() for key in text.split(',') if key.strip()]
    unknown = [key for key in keys if key not in SORTS]
    if unknown or not keys:
        raise argparse.ArgumentTypeError(f"unknown sort key {','.join(unknown)!r}: use {', '.join(SORTS)}, or "
                                         f"several of them comma-separated")
    return keys


def sort_journeys(journeys, by='departure'):
    """by: a SORTS key, or several ('probability,arrival,wait' or a list), most important first"""
    keys = sort_keys(by) if isinstance(by, str) else list(by)
    columns, ascending = [], []
    for key in keys + SORTS[keys[0]][2]:
        for column, up in zip(*SORTS[key][:2]):
            if column not in columns:
                columns.append(column)
                ascending.append(up)
    keyed = journeys.assign(wait=journeys.halt_time.fillna(pd.Timedelta(0)),
                            seats=(-journeys.worstCount).where(journeys.confirmed),
                            waitlistNumber=journeys.worstCount.where(~journeys.confirmed & journeys.probability.isna()))
    order = keyed.sort_values(columns, ascending=ascending, na_position='last').index
    return journeys.loc[order].reset_index(drop=True)


def format_time(timestamp, dt):
    days = (timestamp.date() - dt).days
    return timestamp.strftime('%H:%M') + (f'+{days}' if days else '')


def format_duration(duration):
    minutes = int(duration.total_seconds() // 60)
    return f'{minutes // 60}h{minutes % 60:02d}'


def chance_text(journey):
    if journey.confirmed:
        return 'CNF'
    if pd.notna(journey.probability):
        return f'{journey.probability:.0%}'
    return {STATUS_RANK['RAC']: 'RAC', STATUS_RANK['WL']: 'WL'}.get(journey.worstRank, 'NA')


# (title, alignment); a journey's own columns come first, then one row per train
JOURNEY_COLUMNS = [('#', '>'), ('CHANCE', '<'), ('TOTAL', '>'), ('TIME', '>'), ('TRAIN', '<'), ('NAME', '<'),
                   ('FROM', '<'), ('DEP', '<'), ('TO', '<'), ('ARR', '<'), ('WAIT', '>'), ('CLASS', '<'),
                   ('STATUS', '<'), ('FARE', '>'), ('OTHER CLASSES', '<')]


def journey_rows(number, journey, dt):
    """JOURNEY_COLUMNS rows for one journey: one per train, with the journey's own columns on the first"""
    split = journey.type == 'split'
    trains = [(1, journey.fromStnCode, journey.departure, journey.haltStation if split else journey.toStnCode,
               journey.haltArrival if split else journey.arrival, '')]
    if split:
        trains.append((2, journey.haltStation, journey.haltDeparture, journey.toStnCode, journey.arrival,
                       format_duration(journey.halt_time)))
    rows = []
    for leg, origin, departure, destination, arrival, wait in trains:
        field = lambda name: getattr(journey, f'leg{leg}{name}')
        status = short_status(field('Status')) + (f" {field('Probability'):.0%}" if pd.notna(field('Probability'))
                                                  else '')
        rows.append(['', '', '', '', f"{getattr(journey, f'train{leg}'):05d}", str(field('Name') or '')[:18], origin,
                     format_time(departure, dt), destination, format_time(arrival, dt), wait, field('Class'), status,
                     '' if pd.isna(field('Fare')) else str(field('Fare')), field('Others') or ''])
    rows[0][:4] = [str(number), chance_text(journey), '' if pd.isna(journey.totalFare) else str(journey.totalFare),
                   format_duration(journey.duration)]
    return rows


def format_table(columns, rows, width=None):
    """lines of an aligned text table; rows of None are blank separator lines. Where a line would be longer than
    width, the last column wraps at its ' | ' separators, staying under its title"""
    widths = [max([len(title)] + [len(row[number]) for row in rows if row]) for number, (title, _) in
              enumerate(columns[:-1])]
    indent = sum(widths) + 2 * len(widths)
    lines = []
    for row in [[title for title, _ in columns], ['-' * size for size in widths] + ['-' * len(columns[-1][0])]] + rows:
        if row is None:
            lines.append('')
            continue
        fixed = '  '.join(f"{cell:{align}{size}}" for cell, (_, align), size in zip(row, columns, widths))
        parts = [row[-1]]
        if width and indent + len(row[-1]) > width:
            parts = []
            for piece in row[-1].split(' | '):
                if parts and indent + len(parts[-1]) + 3 + len(piece) <= width:
                    parts[-1] += ' | ' + piece
                else:
                    parts.append(piece)
        lines.append(f"{fixed}  {parts[0]}".rstrip())
        lines += [' ' * indent + part for part in parts[1:]]
    return lines


def print_journeys(journeys, dt, sort_by, limit=None, out=None, logged_in=False):
    def say(*parts):
        print(*parts, file=out or sys.stdout)

    sort_by = sort_keys(sort_by) if isinstance(sort_by, str) else sort_by
    shown = journeys if limit is None else journeys.head(limit)
    say(f"\n{len(journeys)} journeys ({int((journeys.type == 'direct').sum())} direct, "
        f"{int((journeys.type == 'split').sum())} split), {int(journeys.confirmed.sum())} confirmable now; "
        f"sorted by {', '.join(sort_by)}" + (f"; showing first {len(shown)}" if len(shown) < len(journeys) else ''))
    rows = []
    for number, journey in enumerate(shown.itertuples(index=False), 1):
        rows += [None] * (number > 1) + journey_rows(number, journey, dt)
    # on screen, wrap the last column instead of letting the terminal break the table's lines
    width = shutil.get_terminal_size().columns - 1 if out is None and sys.stdout.isatty() else None
    for line in format_table(JOURNEY_COLUMNS, rows, width):
        say(line)
    say("\nCHANCE: CNF = seats now on every train; NN% = IRCTC's confirmation chance (both trains' multiplied on a "
        "split journey); RAC / WL = the worst train's status where IRCTC gave no %; NA = a train can't be booked.\n"
        "Each train's row has its best class (CLASS, STATUS, FARE); OTHER CLASSES has the rest. TOTAL = fare of the "
        "whole journey, WAIT = time at the station between the two trains.")
    if 'probability' in sort_by and journeys[['leg1Probability', 'leg2Probability']].isna().all(axis=None):
        reason = "see the messages above" if logged_in else \
            "IRCTC only gives them to a logged-in session, see --irctc-session"
        say(f"No confirmation percentages ({reason}), so waitlisted journeys are ranked by status: RAC first, then "
            f"the lowest waitlist number.")


def load_predictor(path, parser):
    """WaitlistPredictionClient for the logged-in request copied into path (or the newest .irctc_session* file),
    checked up front so an expired login shows before the minute of availability enquiries, not after"""
    explicit = path is not None
    if not explicit:
        found = sorted(BASE_PATH.glob('.irctc_session*'), key=lambda file: file.stat().st_mtime)
        if not found:
            return None
        path = found[-1]
    try:
        # at most 1 request/s on the logged-in account, separate from the anonymous enquiries
        predictor = WaitlistPredictionClient.from_copied_request(IRCTCClient(min_interval=1.0),
                                                                 path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        if explicit:
            parser.error(f"--irctc-session {path}: {e}")
        print(f"Not using the IRCTC login in {path.name}: {e}")
        return None
    copied = f"copied {datetime.fromtimestamp(path.stat().st_mtime):%d %b %H:%M}"
    error = predictor.check_session()
    if error and any(session_error in error.lower() for session_error in SESSION_ERRORS):
        print(f"The IRCTC login in {path.name} ({copied}) has expired ({error}), so no confirmation chances this "
              f"time. Copy a fresh request from a logged-in irctc.co.in tab into it (devtools > Network > "
              f"right-click a request > Copy as cURL).")
        return None
    print(f"Using the IRCTC login in {path.name} ({copied}) for confirmation chances")
    return predictor


class Tee:
    """file-like object writing to several streams, to keep a copy of what's printed"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def station_list(text):
    return [code.strip().upper() for code in text.split(',') if code.strip()]


def main():
    parser = argparse.ArgumentParser(description="Direct and split (via a halt station) trains with live IRCTC "
                                                 "availability for each leg, filtered and sorted like IRCTC's search")
    parser.add_argument('source', type=station_list,
                        help="station code, e.g. ADI, or several comma-separated (ADI,BRC): each is searched together "
                             "with its nearby stations")
    parser.add_argument('destination', help="station code, e.g. MMCT")
    parser.add_argument('date', type=date.fromisoformat, help="journey date at source, YYYY-MM-DD")
    parser.add_argument('--classes', default=','.join(DEFAULT_CLASSES),
                        help="classes to check, in order of preference (default: %(default)s)")
    parser.add_argument('--quota', default='GN', help="GN (general), TQ (tatkal), PT (premium tatkal), LD, SS...")
    parser.add_argument('--irctc-session', type=Path, metavar='FILE',
                        help="a request copied from a logged-in irctc.co.in tab (devtools > Network > right-click > "
                             "Copy as cURL) to get IRCTC's confirmation chance for waitlisted legs (default: the "
                             "newest .irctc_session* file in the repo folder, if any)")
    parser.add_argument('--no-login', action='store_true',
                        help="don't use an IRCTC login even if there is a session file")
    parser.add_argument('--max-predictions', type=int, default=80,
                        help="most confirmation chances to ask the logged-in account for (1 per second), best "
                             "journeys first (default: %(default)s)")

    filters = parser.add_argument_group('filters')
    filters.add_argument('--type', choices=['all', 'direct', 'split'], default='all',
                         help="direct trains, split journeys via a halt station, or both mixed (default: all)")
    filters.add_argument('--depart-between', type=time_window, metavar='HH:MM-HH:MM',
                         help="departure clock time from source, e.g. 06:00-12:00 (22:00-02:00 wraps past midnight). "
                              "Add +N for days after the journey date: 21:00-10:00+1 also searches trains leaving "
                              "the next morning")
    filters.add_argument('--arrive-between', type=time_window, metavar='HH:MM-HH:MM',
                         help="arrival clock time at destination, e.g. 18:00-23:59 (any day). Add +N for days after "
                              "the journey date: 23:59-10:00+1 = from midnight to 10am the next morning")
    filters.add_argument('--from-stations', type=station_list, metavar='ADI,SBIB',
                         help="only these boarding stations (nearby stations are included by default)")
    filters.add_argument('--to-stations', type=station_list, metavar='MMCT,DDR',
                         help="only these arrival stations (nearby stations are included by default)")
    filters.add_argument('--max-hours', type=float, help="max total journey hours")
    filters.add_argument('--available-only', action='store_true', help="only journeys confirmable now on every leg")
    filters.add_argument('--min-probability', type=float, metavar='PERCENT',
                         help="only journeys with at least this confirmation chance (confirmed seats count as 100)")

    split = parser.add_argument_group('split journeys')
    split.add_argument('--min-halt', type=int, default=30, help="minimum layover in minutes (default: %(default)s)")
    split.add_argument('--max-halt', type=int, default=360, help="maximum layover in minutes (default: %(default)s)")
    split.add_argument('--max-detour', type=float, default=1.25,
                       help="skip split journeys travelling more than this times the shortest route's km "
                            "(default: %(default)s)")
    split.add_argument('--max-split', '--max-indirect', type=int, default=200,
                       help="how many split journeys to check from each source station, fastest first (default: "
                            "%(default)s)")

    output = parser.add_argument_group('output')
    output.add_argument('--sort', type=sort_keys, default='departure',
                        help=f"{', '.join(SORTS)} (default: departure), or several comma-separated, most important "
                             f"first: probability,arrival,wait = best chance, then earliest arrival among equal "
                             f"chances, then shortest wait")
    output.add_argument('--limit', type=int, help="print only the first N journeys (the saved .txt has them all)")
    output.add_argument('--out-dir', type=Path, default=Path('.'),
                        help="where to write journeys_availability.txt (this output) and .csv")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    text_path, csv_path = args.out_dir / "journeys_availability.txt", args.out_dir / "journeys_availability.csv"
    with open(text_path, 'w', encoding='utf-8') as report:
        report.write(f"python {' '.join(sys.argv)}\nrun at {datetime.now():%d %b %Y %H:%M}\n\n")
        with redirect_stdout(Tee(sys.stdout, report)):
            predictor = None if args.no_login else load_predictor(args.irctc_session, parser)
            planner = TripPlannerWithAvailability(class_preference=args.classes.split(','), quota=args.quota,
                                                  predictor=predictor)
            journeys = planner.query(
                args.source, args.destination.upper(), args.date, args.type, args.depart_between,
                args.arrive_between, args.from_stations, args.to_stations, timedelta(minutes=args.min_halt),
                timedelta(minutes=args.max_halt), timedelta(hours=args.max_hours) if args.max_hours else None,
                args.max_split, args.max_predictions, args.max_detour)
            journeys = sort_journeys(filter_availability(journeys, args.available_only, args.min_probability),
                                     args.sort)
        print_journeys(journeys, args.date, args.sort, args.limit, logged_in=predictor is not None)
        print_journeys(journeys, args.date, args.sort, out=report, logged_in=predictor is not None)
    journeys.to_csv(csv_path, index=False)
    print(f"\nSaved this output (every journey) to {text_path} and the table to {csv_path}")


if __name__ == '__main__':
    main()
