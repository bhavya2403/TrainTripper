import json
import joblib
import datetime
import pandas as pd
from urllib.parse import quote_plus
from decouple import config
from SearchAlgorithm.algo_object_oriented import TrainsFinder

base_path = ".."
class_codes = ['1A', '2A', '3A', 'SL', '2S', 'CC']

class TripPlannerWithPrices:
    def __init__(self, database_url, price_model_name):
        self.train_finder = TrainsFinder(database_url)
        self.model = joblib.load(f"{base_path}/Modelling/trained_models/{price_model_name}.pkl")
        self.distance_map: dict
        self.catering_trains: list
        self.reservation_charge: list
        self.superfast_charge: list
        self.train_classes_df: list
        self.precompute()

    def precompute(self):
        map_tmp = json.load(open(f"{base_path}/Data/precomputes/distance_map"))
        keys = list(map(eval, map_tmp.keys()))
        self.distance_map = {key: val for key, val in zip(keys, map_tmp.values())}

        train_classes = json.load(open(f"{base_path}/Data/precomputes/train_classes"))
        dct = {}
        self.train_classes_df = pd.DataFrame(columns=class_codes)
        for tn, codes in train_classes.items():
            for cc in codes:
                dct[len(dct)] = {'trainNumber': int(tn), 'classCode': cc}
        self.train_classes_df = pd.DataFrame(dct).T

        self.catering_trains = json.load(open(f"{base_path}/Data/precomputes/catering_trains.json"))
        self.dynamicfare_trains = json.load(open(f"{base_path}/Data/precomputes/dynamicfare_trains.json"))
        self.reservation_charge = json.load(open(f"{base_path}/Data/precomputes/reservation_charge.json"))
        self.superfast_charge = json.load(open(f"{base_path}/Data/precomputes/superfast_charge.json"))

    def get_basic_df_direct(self, direct_trains):
        return direct_trains[['trainNumber', 'fromStnCode', 'toStnCode']]

    def get_basic_df_indirect(self, indirect_trains):
        df1 = indirect_trains[['trainNumber1', 'fromStnCode', 'haltStation']]
        df1.rename({'trainNumber1': 'trainNumber', 'haltStation': 'toStnCode'}, axis=1, inplace=True)
        df2 = indirect_trains[['trainNumber2', 'haltStation', 'toStnCode']]
        df2.rename({'trainNumber2': 'trainNumber', 'haltStation': 'fromStnCode'}, axis=1, inplace=True)
        basic_df = pd.concat([df1, df2])
        return basic_df.reset_index(drop=True)

    def add_classes(self, basic_df):
        merged = pd.merge(basic_df, self.train_classes_df, 'inner')
        dummies = pd.get_dummies(merged.classCode)
        for cc in class_codes:
            if cc not in dummies.columns:
                dummies[cc] = False
        return pd.concat([merged.drop(['classCode'], axis=1), dummies], axis=1)

    def add_dynamics_catering(self, trains):
        trains['if_dynamic_fare'] = trains['trainNumber'].isin(self.dynamicfare_trains)
        trains['if_offering_catering'] = trains['trainNumber'].isin(self.catering_trains)
        return trains

    def add_distance_duration(self, trains):
        def apply_func(row):
            key1 = self.distance_map.get((row['trainNumber'], row['fromStnCode']), None)
            key2 = self.distance_map.get((row['trainNumber'], row['toStnCode']), None)
            return pd.Series({'distance': key2[0] - key1[0], 'duration': key2[1] - key1[1]})
        distance_duration = trains.apply(apply_func, axis=1)
        return pd.concat([trains, distance_duration], axis=1)

    def get_prices_from_model(self, df):
        return self.model.predict(df[self.model.feature_names_in_])

    def set_prices_direct(self, direct_trains, predictions):
        direct_trains_dct = direct_trains.T.to_dict()
        drop = []
        for key, row in direct_trains_dct.items():
            if (row['trainNumber'], row['fromStnCode'], row['toStnCode']) in predictions.index:
                df_tmp = predictions.loc[(row['trainNumber'], row['fromStnCode'], row['toStnCode'])]
                direct_trains_dct[key]['prices'] = df_tmp.to_json(orient='records')
            else:
                drop.append(key)
        return {k: v for k, v in direct_trains_dct.items() if k not in drop}

    def set_prices_indirect(self, indirect_trains, predictions):
        indirect_trains_dct = indirect_trains.T.to_dict()
        drop = []
        for key, row in indirect_trains_dct.items():
            if ((row['trainNumber1'], row['fromStnCode'], row['haltStation']) in predictions.index and
                    (row['trainNumber2'], row['haltStation'], row['toStnCode']) in predictions.index):
                df_tmp = predictions.loc[(row['trainNumber1'], row['fromStnCode'], row['haltStation'])]
                indirect_trains_dct[key]['price1'] = df_tmp.to_json(orient='records')
                df_tmp = predictions.loc[(row['trainNumber2'], row['haltStation'], row['toStnCode'])]
                indirect_trains_dct[key]['price2'] = df_tmp.to_json(orient='records')
            else:
                drop.append(key)
        return {k: v for k, v in indirect_trains_dct.items() if k not in drop}

    def format_predictions(self, df_pred):
        df_pred['classCode'] = df_pred[class_codes].idxmax(axis=1)
        return df_pred[['classCode', 'price']]

    def query(self, source_station: str, dest_station: str, date_obj: datetime.date):
        direct_trains, indirect_trains = self.train_finder.multi_train_itineraries(source_station, dest_station, date_obj)

        df_pred_direct = self.add_distance_duration(self.add_dynamics_catering(
            self.add_classes(self.get_basic_df_direct(direct_trains))))
        df_pred_indirect = self.add_distance_duration(self.add_dynamics_catering(
            self.add_classes(self.get_basic_df_indirect(indirect_trains))))
        df_pred = pd.concat([df_pred_direct, df_pred_indirect]).drop_duplicates()
        df_pred.set_index(['trainNumber', 'fromStnCode', 'toStnCode'], inplace=True)

        df_pred['price'] = self.get_prices_from_model(df_pred)
        predictions = self.format_predictions(df_pred)
        direct_trains_dct = self.set_prices_direct(direct_trains, predictions)
        indirect_trains_dct = self.set_prices_indirect(indirect_trains, predictions)
        return direct_trains_dct, indirect_trains_dct

database_url="postgresql://postgres:%s@localhost:5432/traintripper" % quote_plus(config("POSTGRES_PASSWORD"))
planner = TripPlannerWithPrices(database_url, "decision_tree_regression")
direct_trains_dct, indirect_trains_dct= planner.query("MMCT", "NDLS", datetime.date(2023, 11, 8))