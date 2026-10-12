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


class BulkAvailabilityClient:
    # availability and fare of many legs (train, from, to, date, classes), mixed station pairs allowed, in one
    # request: what IRCTC's train list uses to fill itself in. IRCTC answers 403 for much more than ~25 legs
    BASE_URL = "https://www.irctc.co.in/eticketing/protected/mapps1/allLapAvlEnq"
    MAX_LEGS = 20
    irctcClient: IRCTCClient

    def __init__(self, irctcClient: IRCTCClient):
        self.irctcClient = irctcClient

    def request_wrapper(self, legs, quota_code):
        data = {"alternateAvlInputDTO": [{
            "trainNo": train_number,
            "destStn": destination_station,
            "srcStn": source_station,
            "ftBooking": False,
            "jrnyDate": date,
            "quotaCode": quota_code,
            "concessionPassengers": False,
            "avlClasses": class_codes
        } for train_number, source_station, destination_station, date, class_codes in legs]}
        return self.irctcClient.send_post_request(self.BASE_URL, data)

    @staticmethod
    def as_list(value):
        return value if isinstance(value, list) else [] if value is None else [value]

    def filter_data(self, responseJson, legs):
        """per leg, {class_code: what TrainPriceAndAvailClient.get() returns for it}, or None where IRCTC's answer
        has nothing for the leg"""
        answers = {}
        for train in self.as_list(responseJson.get("trainBtwnStnsList")):
            classes = {}
            for dto in self.as_list(train.get("classAvlDTO")):
                if "errorMessage" in dto:
                    classes[dto.get("enqClass")] = {"errorMessage": str(dto["errorMessage"]).strip()}
                else:
                    classes[dto.get("enqClass")] = {
                        **{k: v for k, v in dto.items() if k in TrainPriceAndAvailClient.KEEP_LIST},
                        "availability": list(map(TrainPriceAndAvailClient.filter_day, self.as_list(dto.get("avlDayList"))))}
            if classes:
                answers[(train.get("trainNumber"), train.get("fromStnCode"), train.get("toStnCode"),
                         str(train.get("journeyDate"))[:10].replace("-", ""))] = classes
        return [answers.get((train_number, source_station, destination_station, date))
                for train_number, source_station, destination_station, date, _ in legs]

    def get(self, legs, quota_code="GN"):
        """legs: up to MAX_LEGS of (5-digit train number, from, to, yyyymmdd boarding date, [class codes])"""
        return self.filter_data(self.request_wrapper(legs, quota_code), legs)


class WaitlistPredictionClient:
    # IRCTC's "confirmation chance" for a waitlisted status, as the train list shows it. IRCTC only answers a logged-in
    # user (anonymous requests get "Please verify your mobile number..."), so this sends that user's session headers,
    # taken from any request the browser made while logged in (devtools > Network > Copy as cURL)
    BASE_URL = "https://www.irctc.co.in/eticketing/protected/mapps1/predictCnfChance"
    SESSION_HEADERS = ("authorization", "greq", "bmiyek")
    # the prediction service still knows the Mumbai terminals by their old codes, like the website sends them
    OLD_CODES = {"MMCT": "BCT", "CSMT": "CSTM"}
    irctcClient: IRCTCClient

    def __init__(self, irctcClient: IRCTCClient, session_headers, sample_request=None):
        self.irctcClient = irctcClient
        self.session_headers = {**session_headers, "referer": "https://www.irctc.co.in/nget/booking/train-list"}
        # a prediction request the browser itself sent, replayed by check_session()
        self.sample_request = sample_request

    @classmethod
    def from_copied_request(cls, irctcClient, text):
        # works for "Copy as cURL" (bash or cmd) and "Copy as PowerShell"
        headers = {}
        for name, value in re.findall(r"(?i)\b(authorization|greq|bmiyek)[\"'^]*\s*[:=]\s*[\"'^]*([^\"'^\r\n]+)", text):
            headers.setdefault(name.lower(), value.strip())
        missing = [name for name in cls.SESSION_HEADERS if not headers.get(name)]
        if missing:
            raise ValueError(f"no {', '.join(missing)} header in the copied request; copy one sent while logged in")
        sample_request = None
        body = re.search(r"--data-raw\s+\$?'(\{.*?\})'", text)
        if "predictCnfChance" in text and body:
            try:
                sample_request = json.loads(body.group(1))
            except ValueError:
                pass
        return cls(irctcClient, headers, sample_request)

    def check_session(self):
        """None if the logged-in session still works, else IRCTC's error. Only possible when the copied request was
        a prediction itself; otherwise assumes the session is fine"""
        if not self.sample_request:
            return None
        response = self.irctcClient.send_post_request(self.BASE_URL, self.sample_request, self.session_headers)
        return None if response.get("probability") is not None else str(response.get("errorMessage", response))

    @staticmethod
    def status_code(status):
        return "W" if "WL" in status else "R" if "RAC" in status else "C" if "CNF" in status else ""

    @staticmethod
    def status_number(status):
        return int(re.match(r"\D*(\d*)", status).group(1) or 0)

    def request_wrapper(self, train_number, date, source_station, destination_station, class_code, status, wl_type,
                        site_id, quota_code):
        # status is IRCTC's "<booking status>/<current status>", e.g. "RLWL16/WL11"
        running, _, current = status.partition("/")
        current = current or running
        # the website sends a JS Date in UTC; noon IST is the same calendar day in both zones
        day = datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%dT06:30:00.000Z")
        data = {
            "bookingDate": day,
            "currentStatus": self.status_code(current),
            "currentStatusNumber": self.status_number(current),
            "fromStnCode": self.OLD_CODES.get(source_station, source_station),
            "journeyClass": class_code,
            "journeyDate": day,
            "quota": quota_code,
            "runningStatus": self.status_code(running),
            "runningStatusNumber": self.status_number(running),
            "siteId": site_id,
            "toStnCode": self.OLD_CODES.get(destination_station, destination_station),
            "trainNumber": train_number,
            "wlType": wl_type,
            "isLogedinReq": False
        }
        return self.irctcClient.send_post_request(self.BASE_URL, data, self.session_headers)

    def filter_data(self, responseJson):
        if responseJson.get("probability") is None:
            return {"errorMessage": str(responseJson.get("errorMessage", "IRCTC gave no probability")).strip()}
        return {"probability": float(responseJson["probability"]), "chartStatus": responseJson.get("passengerChartStatus")}

    def get(self, train_number, date, source_station, destination_station, class_code, status, wl_type, site_id,
            quota_code="GN"):
        return self.filter_data(self.request_wrapper(train_number, date, source_station, destination_station,
                                                     class_code, status, wl_type, site_id, quota_code))


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
