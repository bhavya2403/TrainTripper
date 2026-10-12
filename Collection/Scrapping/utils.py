import json
import re
import threading
from datetime import datetime
from time import time, sleep

from curl_cffi import requests

# IRCTC sits behind Akamai, which silently drops clients whose TLS/HTTP2 fingerprint is not a browser's
# (plain `requests` just times out). curl_cffi impersonates Chrome's fingerprint; a GET of the homepage
# then hands out the Akamai cookies (_abck, bm_sz, ...) that the JSON endpoints expect.
class IRCTCClient:
    HOME_URL = "https://www.irctc.co.in/nget/train-search"
    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "bmirak": "webbm",
        "content-language": "en",
        "content-type": "application/json; charset=UTF-8",
        "origin": "https://www.irctc.co.in",
        "referer": "https://www.irctc.co.in/nget/train-search",
    }

    def __init__(self, min_interval=0.25, timeout=30, retries=3, impersonate="chrome"):
        # min_interval is shared by all threads using this client, so it caps the total request rate
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.impersonate = impersonate
        self._local = threading.local()
        self._rate_lock = threading.Lock()
        self._next_slot = 0.0

    def _session(self, fresh=False):
        if fresh or getattr(self._local, "session", None) is None:
            session = requests.Session(impersonate=self.impersonate)
            session.get(self.HOME_URL, timeout=self.timeout)
            self._local.session = session
        return self._local.session

    def _throttle(self):
        with self._rate_lock:
            now = time()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            sleep(wait)

    def _headers(self):
        return {**self.HEADERS, "greq": str(int(time() * 1000))}

    def _send(self, method, url, data=None, headers=None):
        error = "Request to IRCTC failed"
        for attempt in range(self.retries):
            try:
                self._throttle()
                session = self._session(fresh=attempt > 0)
                response = session.request(method, url, headers={**self._headers(), **(headers or {})}, json=data,
                                           timeout=self.timeout)
                if response.status_code == 200:
                    return response.json()
                error = f"Request to IRCTC failed with status {response.status_code}"
                if response.status_code == 401:
                    break  # logged-in session expired; asking again won't help
            except Exception as e:
                error = f"Request to IRCTC failed: {e}"
            sleep(2 ** attempt)
        return {"errorMessage": error}

    def send_post_request(self, url, data, headers=None):
        return self._send("POST", url, data, headers)

    def send_get_request(self, url):
        return self._send("GET", url)


class TrainListClient:
    BASE_URL = "https://www.irctc.co.in/eticketing/protected/mapps1/altAvlEnq/TC"
    FILTER_LIST = ["trainType", "atasOpted", "flexiFlag", "trainOwner", "trainsiteId"]
    irctcClient = None

    def __init__(self, irctcClient: IRCTCClient):
        self.irctcClient = irctcClient

    def filter_data(self, responseJson):
        try:
            return list(map(lambda d: {param: d[param] for param in d if param not in self.FILTER_LIST},
                            responseJson["trainBtwnStnsList"]))
        except:
            return []

    def request_wrapper(self, source_station, destination_station, date, quota_code):
        data={
            "concessionBooking": False,
            "srcStn": source_station,
            "destStn": destination_station,
            "jrnyClass": "",
            "jrnyDate": date,
            "quotaCode": quota_code,
            "currentBooking": False,
            "flexiFlag": False,
            "handicapFlag": False,
            "ticketType": "E",
            "loyaltyRedemptionBooking": False,
            "ftBooking": False
        }
        return self.irctcClient.send_post_request(self.BASE_URL, data)

    def get(self, source_station, destination_station, date, quota_code="GN"):
        return self.filter_data(self.request_wrapper(source_station, destination_station, date, quota_code))


class TrainPriceAndAvailClient:
    # date is the journey date at the boarding station (source_station), not at the train's origin
    BASE_URL = "https://www.irctc.co.in/eticketing/protected/mapps1/avlFarenquiry/"
    KEEP_LIST = ['baseFare', 'reservationCharge', 'superfastCharge', 'fuelAmount', 'totalConcession', 'tatkalFare',
                 'serviceTax', 'otherCharge', 'cateringCharge', 'dynamicFare', 'totalFare']
    irctcClient: IRCTCClient

    def __init__(self, irctcClient: IRCTCClient):
        self.irctcClient = irctcClient

    def request_wrapper(self, train_number, date, source_station, destination_station, class_code, quota_code):
        url=f"{self.BASE_URL}{train_number}/{date}/{source_station}/{destination_station}/{class_code}/{quota_code}/N"
        data={
            "paymentFlag": "N",
            "concessionBooking": False,
            "ftBooking": False,
            "loyaltyRedemptionBooking": False,
            "ticketType": "E",
            "quotaCode": quota_code,
            "moreThanOneDay": True,
            "trainNumber": train_number,
            "fromStnCode": source_station,
            "toStnCode": destination_station,
            "isLogedinReq": False,
            "journeyDate": date,
            "classCode": class_code
        }
        return self.irctcClient.send_post_request(url, data)

    def filter_data(self, responseJson):
        if "errorMessage" in responseJson:
            return {"errorMessage": str(responseJson["errorMessage"]).strip()}
        try:
            availability = responseJson["avlDayList"]
            answer = {k: v for k, v in responseJson.items() if k in self.KEEP_LIST}
            answer["availability"] = list(map(self.filter_day, availability))
            return answer
        except:
            return {"errorMessage": "Data couldn't be filtered according to requirements"}

    @staticmethod
    def filter_day(d):
        day = {"date": d['availablityDate'], "status": d["availablityStatus"]}
        # wlType (which waitlist the status is on) feeds WaitlistPredictionClient; IRCTC adds a 0-1 confirmation
        # "probability" only for some sessions, so both are optional
        if d.get("wlType") is not None:
            day["wlType"] = d["wlType"]
        if d.get("probability") is not None:
            day["probability"] = float(d["probability"])
        return day

    def get(self, train_number, date, source_station, destination_station, class_code, quota_code="GN"):
        return self.filter_data(
            self.request_wrapper(train_number, date, source_station, destination_station, class_code, quota_code))


class TrainScheduleClient:
    BASE_URL = "https://www.irctc.co.in/eticketing/protected/mapps1/trnscheduleenquiry/"
    irctcClient: IRCTCClient

    def __init__(self, irctcClient: IRCTCClient):
        self.irctcClient=irctcClient

    def request_wrapper(self, train_number):
        url = f"{self.BASE_URL}{train_number}"
        return self.irctcClient.send_get_request(url)

    def get(self, train_number):
        return self.request_wrapper(train_number)

def main():
    irctc_client = IRCTCClient()
    irctc_train_list_client = TrainListClient(irctc_client)
    irctc_price_client = TrainPriceAndAvailClient(irctc_client)
    print(irctc_train_list_client.get("ADI", "BRC", "20231121", "GN"))
    print(irctc_price_client.get("20902", "20230921", "ADI", "BRC", "CC", "GN"))
