"""
Scrapes train schedules from IRCTC into Data/irctc/schedules.json (one raw IRCTC response per line).

Resumable: every train number IRCTC gave a definite answer for (a schedule, or "not a valid train number") is logged in
Data/irctc/schedules_checked.txt and skipped on reruns; network failures are not logged, so a rerun retries them.
    python save_train_schedule.py --seeds                    # numbers known from the 2023 dataset, OGD, rapid-api, ...
    python save_train_schedule.py --ranges 0-29999           # brute-force sweep
    python save_train_schedule.py --seeds --ranges 0-29999,50000-79999
Then rebuild the CSVs the search uses with DatabaseCreation/build_database.py.
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
from time import time

from utils import *

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SCHEDULES_PATH = os.path.join(ROOT, 'Data', 'irctc', 'schedules.json')
CHECKED_PATH = os.path.join(ROOT, 'Data', 'irctc', 'schedules_checked.txt')
# some IRCTC servers answer in Hindi: "please enter a valid train number"
INVALID_TRAIN = {'Please enter a valid train number', 'कृपया वैध गाड़ी संख्या दर्ज करें'}


def normalize(train_number):
    # IRCTC wants 5 digits ("00961", not "961"); entries like "17481-Slip" are coaches of a train, not trains
    train_number = str(train_number).strip("' ")
    return train_number.zfill(5) if re.fullmatch(r'\d{1,5}', train_number) else None


def seed_train_numbers():
    import pandas as pd  # only needed here; keeps long sweeps from holding pandas/numpy loaded
    seeds = set()

    # 2023 dataset, from git since the working-tree copy is being rewritten
    old = subprocess.run(['git', 'show', 'HEAD:Data/irctc/schedules.json'], cwd=ROOT, capture_output=True,
                         encoding='utf-8')
    for line in old.stdout.splitlines():
        try:
            seeds.add(json.loads(line)['trainNumber'])
        except (ValueError, KeyError):
            continue

    # open government data
    ogd = os.path.join(ROOT, 'Data', 'ogd')
    seeds.update(pd.read_csv(os.path.join(ogd, 'Train_details_22122017.csv'), dtype=str)['Train No'].dropna())
    seeds.update(pd.read_csv(os.path.join(ogd, 'isl_wise_train_detail_03082015_v1.csv'), dtype=str)['Train No.'].dropna())

    # rapid-api trains at stations
    with open(os.path.join(ROOT, 'Data', 'rapid-api', 'station_trains.json')) as f:
        station_trains = json.load(f)
    for val in station_trains.values():
        for trains in (val.get('data') or {}).values():
            seeds.update(train['trainNo'] for train in trains)

    # railways-master dataset
    with open(os.path.join(ROOT, 'Data', 'railways-master', 'trains.json')) as f:
        trains = json.load(f)
    seeds.update(feature['properties']['number'] for feature in trains['features'])

    return sorted(set(filter(None, map(normalize, seeds))))


def parse_ranges(ranges):
    numbers = []
    for part in ranges.split(','):
        start, end = map(int, part.split('-'))
        numbers += [str(n).zfill(5) for n in range(start, end + 1)]
    return numbers


def already_done():
    done = set()
    if os.path.exists(CHECKED_PATH):
        with open(CHECKED_PATH) as f:
            done.update(line.strip() for line in f if line.strip())
    if os.path.exists(SCHEDULES_PATH):
        with open(SCHEDULES_PATH, encoding='utf-8') as f:
            for line in f:
                try:
                    done.add(json.loads(line)['trainNumber'])
                except (ValueError, KeyError):
                    continue
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seeds', action='store_true', help='train numbers known from older datasets')
    parser.add_argument('--ranges', default='', help='comma separated inclusive ranges, e.g. 0-29999,50000-79999')
    parser.add_argument('--rate', type=float, default=4, help='max total requests per second')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--max-failures', type=int, default=25, help='abort after this many failures in a row')
    args = parser.parse_args()
    if not args.seeds and not args.ranges:
        parser.error('pass --seeds and/or --ranges')
    # IRCTC error messages can contain Hindi, which a redirected Windows console (cp1252) cannot encode
    sys.stdout.reconfigure(errors='backslashreplace')

    done = already_done()
    todo = (seed_train_numbers() if args.seeds else []) + (parse_ranges(args.ranges) if args.ranges else [])
    todo = [tn for tn in dict.fromkeys(todo) if tn not in done]
    n = len(todo)
    print(f'{len(done)} train numbers already checked, {n} to go')
    if not n:
        return

    scheduleClient = TrainScheduleClient(IRCTCClient(min_interval=1 / args.rate))
    tasks, results = queue.Queue(), queue.Queue()
    for tn in todo:
        tasks.put(tn)
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                tn = tasks.get_nowait()
            except queue.Empty:
                return
            results.put((tn, scheduleClient.get(tn)))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for thread in threads:
        thread.start()

    schedule_file = open(SCHEDULES_PATH, 'a', encoding='utf-8')
    checked_file = open(CHECKED_PATH, 'a')
    start = time()
    valid = consecutive_failures = 0
    failures = {}
    i = 0
    try:
        while i < n:
            try:
                tn, schedule = results.get(timeout=5)
            except queue.Empty:
                if not any(thread.is_alive() for thread in threads):
                    break
                continue
            i += 1
            if 'stationList' in schedule:
                json.dump(schedule, schedule_file)
                schedule_file.write('\n')
                schedule_file.flush()
                valid += 1
            message = str(schedule.get('errorMessage', '')).strip()
            # a few trains come back with their details but no stationList: a definite answer, just no route
            if 'stationList' in schedule or message in INVALID_TRAIN or ('trainNumber' in schedule and not message):
                checked_file.write(f'{tn}\n')
                checked_file.flush()
                consecutive_failures = 0
            else:
                failures[message[:80]] = failures.get(message[:80], 0) + 1
                consecutive_failures += 1
                if consecutive_failures >= args.max_failures:
                    stop.set()
                    print(f'\nAborting: {consecutive_failures} failures in a row, last: {message!r}. '
                          f'IRCTC may be throttling; rerun later to resume.')
                    sys.exit(1)

            end = time()
            line = (f"Percentage done: {round(i / n * 100, 2)}%, "
                    f"time taken: {int(end - start)}s, "
                    f"time left: {int((n - i) * (end - start) / i)}s, "
                    f"valid: {valid}, failed: {sum(failures.values())}, current train: {tn}")
            if sys.stdout.isatty():
                print(f'\r{line}', end='')
            elif i % 250 == 0 or i == n:
                print(line, flush=True)
    except KeyboardInterrupt:
        stop.set()
        print('\nInterrupted; rerun to resume.')
    finally:
        schedule_file.close()
        checked_file.close()
        print(f'\nDone {i}/{n}: {valid} valid schedules, failures: {failures or "none"}')


if __name__ == '__main__':
    main()
