import json
import joblib
from datetime import date, datetime
import pandas as pd
from SearchAlgorithm.algo_object_oriented import TrainsFinder

class DataPipeline:
    def __init__(self, base_path, class_preference):
        map_tmp = json.load(open(f"{base_path}/Data/precomputes/distance_map"))
        keys = list(map(eval, map_tmp.keys()))
        self.distance_map = {key: val for key, val in zip(keys, map_tmp.values())}

        train_classes = json.load(open(f"{base_path}/Data/precomputes/train_classes"))
        dct = {}
        self.class_preference = class_preference
        self.train_classes_df = pd.DataFrame(columns=self.class_preference)
        for tn, codes in train_classes.items():
            for cc in codes:
                dct[len(dct)] = {'trainNumber': int(tn), 'classCode': cc}
        self.train_classes_df = pd.DataFrame(dct).T

        self.catering_trains = {tuple(l) for l in json.load(open(f"{base_path}/Data/precomputes/catering_trains.json"))}
        self.dynamicfare_trains = {tuple(l) for l in json.load(open
                                                               (f"{base_path}/Data/precomputes/dynamicfare_trains.json"))}
        self.reservation_charge = json.load(open(f"{base_path}/Data/precomputes/reservation_charge.json"))
        self.superfast_charge = json.load(open(f"{base_path}/Data/precomputes/superfast_charge.json"))

    def add_classes(self, basic_df):
        merged = pd.merge(basic_df, self.train_classes_df, 'inner')
        merged['classCode'] = pd.Categorical(merged.classCode, categories=self.class_preference, ordered=True)
        merged.sort_values('classCode', inplace=True)
        merged.drop_duplicates(subset=['trainNumber', 'fromStnCode', 'toStnCode'], inplace=True)
        dummies = pd.get_dummies(merged.classCode)
        for cc in self.class_preference:
            if cc not in dummies.columns:
                dummies[cc] = False
        return pd.concat([merged.drop(['classCode'], axis=1), dummies], axis=1)

    def add_dynamics_catering(self, trains):
        trains['classCode'] = trains[self.class_preference].idxmax(axis=1)
        trains['if_dynamic_fare'] = trains.apply(lambda row: (row['trainNumber'], row['classCode']) in
                                                             self.dynamicfare_trains, axis=1)
        trains['if_offering_catering'] = trains.apply(lambda row: (row['trainNumber'], row['classCode']) in
                                                                  self.catering_trains, axis=1)
        return trains

    def add_distance_duration(self, trains):
        def apply_func(row):
            key1 = self.distance_map.get((row['trainNumber'], row['fromStnCode']), None)
            key2 = self.distance_map.get((row['trainNumber'], row['toStnCode']), None)
            return pd.Series({'distance': key2[0] - key1[0], 'duration': key2[1] - key1[1]})
        distance_duration = trains.apply(apply_func, axis=1)
        return pd.concat([trains, distance_duration], axis=1)

    def send_input(self, input_df):
        return self.add_distance_duration(self.add_dynamics_catering(self.add_classes(input_df)))

    def send_output(self, input_df, output_series):
        output_series = output_series + input_df.classCode.apply(lambda cc: self.reservation_charge[cc])
        output_series = output_series + input_df.apply(lambda row: self.superfast_charge[
            row['trainNumber']][row['classCode']] if row['trainNumber'] in self.superfast_charge else 0, axis=1)
        return output_series

class TripPlannerWithPrices:
    def __init__(self, base_path, price_model_name, class_preference):
        self.train_finder = TrainsFinder(base_path)
        self.pipeline = DataPipeline(base_path, class_preference)
        self.model = joblib.load(f"{base_path}/Modelling/trained_models/{price_model_name}.pkl")

    def get_basic_df_direct(self, direct_trains):
        return direct_trains[['trainNumber', 'fromStnCode', 'toStnCode']]

    def get_basic_df_indirect(self, indirect_trains):
        df1 = indirect_trains[['trainNumber1', 'fromStnCode', 'haltStation']]
        df1.rename({'trainNumber1': 'trainNumber', 'haltStation': 'toStnCode'}, axis=1, inplace=True)
        df2 = indirect_trains[['trainNumber2', 'haltStation', 'toStnCode']]
        df2.rename({'trainNumber2': 'trainNumber', 'haltStation': 'fromStnCode'}, axis=1, inplace=True)
        basic_df = pd.concat([df1, df2])
        return basic_df.reset_index(drop=True)

    def set_prices_direct(self, direct_trains, predictions):
        direct_trains = pd.merge(direct_trains, predictions, how='left')
        return direct_trains.dropna(subset=['price'])

    def set_prices_indirect(self, indirect_trains, predictions):
        indirect_trains = pd.merge(indirect_trains, predictions, left_on=['trainNumber1', 'fromStnCode', 'haltStation'],
                                 right_on=['trainNumber', 'fromStnCode', 'toStnCode'], how='left')
        indirect_trains = pd.merge(indirect_trains, predictions, left_on=['trainNumber2', 'haltStation', 'toStnCode_x'],
                                 right_on=['trainNumber', 'fromStnCode', 'toStnCode'], how='left')
        indirect_trains.rename(columns={'fromStnCode_x': 'fromStnCode', 'classCode_x': 'classCode1',
                                        'price_x': 'price1', 'classCode_y': 'classCode2', 'price_y': 'price2'},
                               inplace=True)
        indirect_trains.drop(columns=['trainNumber_x', 'toStnCode_x', 'toStnCode_y', 'trainNumber_y', 'fromStnCode_y'],
                             inplace=True)
        indirect_trains['price'] = indirect_trains.price1+indirect_trains.price2
        return indirect_trains.dropna(subset=['price'])

    def format_predictions(self, df_pred):
        return df_pred[['trainNumber', 'fromStnCode', 'toStnCode', 'classCode', 'price']]

    def query(self, source_station: str, dest_station: str, date_obj: datetime.date):
        direct_trains, indirect_trains = self.train_finder.multi_train_itineraries(source_station, dest_station, date_obj)

        df_pred_direct =self.get_basic_df_direct(direct_trains)
        df_pred_indirect = self.get_basic_df_indirect(indirect_trains)
        df_pred = pd.concat([df_pred_direct, df_pred_indirect]).drop_duplicates()

        df_pred = self.pipeline.send_input(df_pred)
        df_pred['price'] = self.model.predict(df_pred[self.model.feature_names_in_])
        df_pred['price'] = self.pipeline.send_output(df_pred, df_pred.price)
        predictions = self.format_predictions(df_pred)
        return self.set_prices_direct(direct_trains, predictions), self.set_prices_indirect(indirect_trains, predictions)

def main():
    class_preference = ['SL', '3A', '2A', '1A', '2S', 'CC']
    planner = TripPlannerWithPrices('..', "decision_tree_regression_balanced_price", class_preference)
    direct_trains, indirect_trains = planner.query("MMCT", "NDLS", date(2023, 12, 13))
    direct_trains.to_csv("direct_trains.csv", index=False)
    indirect_trains.to_csv("indirect_trains.csv", index=False)
main()