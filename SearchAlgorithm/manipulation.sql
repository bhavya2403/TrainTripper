insert into trainschedules values ('MJ', null, '8:30:00', 1, 0, 1, 961, -1);

select count(*) from trainschedules;

select *
from trainschedules
where station_code='ADI'
or station_code='ST';


delete from nearbystations where true;

select * from trainschedules where station_code in ('ADI', 'BRC');

create table nearbystation1 as
    select distinct on (station1, station2) * from nearbystations;
alter table nearbystation1 rename to nearbystations;
drop table nearbystations;

select count(*) from nearbystations;

-- INSERT INTO nearbystations (station1, station2, distance)
SELECT DISTINCT station_code AS station1, station_code AS station2, 0 AS distance
FROM trainschedules
where station_code='MMCT';

select train_number, max(route_number) r from trainschedules group by train_number order by r desc;

select * from trainschedules where station_code in ('ADI', 'SBIB', 'SAU', 'ND');

delete from nearbystations
where distance > 30;

select count(*) from nearbystations;

select distinct train_number from trainschedules where route_number>1;