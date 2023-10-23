import requests
import json

# rapid-api
station_codes = ["MAS", "NDLS", "HWH", "BCT", "SBC", "LKO", "JP", "HYB", "PNBE", "BBS", "ADI", "BPL", "ASR", "CSTM", "NZM", "GKP", "TVC", "JAT", "RNC", "DBRG"]

url="https://irctc1.p.rapidapi.com/api/v3/getTrainsByStation"
stationTrains = {}
for station_code in station_codes:
    querystring={"stationCode": station_code}
    headers={
        "X-RapidAPI-Key": "74a525211cmsh209c0af58991434p1e0ae0jsne4f7865d053f",
        "X-RapidAPI-Host": "irctc1.p.rapidapi.com"
    }
    response=requests.get(url, headers=headers, params=querystring)
    stationTrains[station_code] = response.json()
json.dump(stationTrains, open("ML/Project/Data/station_trains.json", "w"))

# https://github.com/datameet/railways