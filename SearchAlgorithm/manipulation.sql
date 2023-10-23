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
alter table nearbystations rename to nearbystations;
drop table nearbystations;

select count(*) from nearbystations;

INSERT INTO nearbystations (station1, station2, distance)
SELECT DISTINCT station_code AS station1, station_code AS station2, 0 AS distance
FROM trainschedules;