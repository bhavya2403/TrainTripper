create table TrainSchedules (
    station_code varchar(10),
    arrival_time time,
    departure_time time,
    route_number int2,
    distance int,
    day_count int4,
    train_number int,
    halt_time_minutes int,
    foreign key (train_number) references trains(train_number)
);
create table trains (
    train_number int,
    train_name varchar(50),
    station_from varchar(10),
    station_to varchar(10),
    trainRunsOnMon boolean,
    trainRunsOnTue boolean,
    trainRunsOnWed boolean,
    trainRunsOnThu boolean,
    trainRunsOnFri boolean,
    trainRunsOnSat boolean,
    trainRunsOnSun boolean
);
alter table trains add primary key (train_number);

create index index_trains_tn on trains(train_number);
create index index_trainschedules_tn on trainschedules(train_number);
create index index_trainschedules_sc on trainschedules(station_code);

create table stations (
    station_code varchar(10) primary key,
    station_name varchar(50)
);

alter table nearbystations add foreign key (station1) references stations(station_code);
alter table nearbystations add foreign key (station2) references stations(station_code);

alter table trainschedules add foreign key (station_code) references stations(station_code);

-- if at least one train runs between stations taking less than 1hr time
create table nearbystations (
    station1 varchar(10),
    station2 varchar(10),
    distance int,
    foreign key (station1) references stations(station_code),
    foreign key (station2) references stations(station_code)
);

select * from nearbystations where distance = (select max(distance) from nearbystations);