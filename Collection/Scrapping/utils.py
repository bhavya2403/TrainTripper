import requests

class IRCTCClient:
    HEADERS = {
        "accept": "application/json, text/plain, */*",
        "bmirak": "webbm",
        "bmiyek": "2482A1E7A31DD4535864E31966B58B93",
        "content-language": "en",
        "content-type": "application/json; charset=UTF-8",
        "greq": "DM01AP04MS3:23deded6-f0ad-4f8a-ba1b-0445bcbf2e22",
        "sec-ch-ua": "\"Microsoft Edge\";v=\"119\", \"Chromium\";v=\"119\", \"Not?A_Brand\";v=\"24\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\""
    }

    def send_post_request(self, url, data):
        try:
            response = requests.post(url, headers=self.HEADERS, json=data)
            return response.json() if response.status_code==200 else {"errorMessage": "Request to IRCTC failed"}
        except Exception as e:
            return {"errorMessage": e}

    def send_get_request(self, url):
        try:
            response = requests.get(url, headers=self.HEADERS)
            return response.json() if response.status_code==200 else {"errorMessage": "Request to IRCTC failed"}
        except Exception as e:
            return {"errorMessage": e}


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
            return responseJson
        try:
            availability = responseJson["avlDayList"]
            answer = {k: v for k, v in responseJson.items() if k in self.KEEP_LIST}
            answer["availability"] = list(map(lambda d: {"date": d['availablityDate'], "status": d["availablityStatus"]}, availability))
            return answer
        except:
            return {"errorMessage": "Data couldn't be filtered according to requirements"}

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
