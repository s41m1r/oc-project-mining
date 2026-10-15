# Sample OCEL

`MESOS-1.0.0.sqlite` is an OCEL 2.0 (SQLite format) of release 1.0.0 of the
Apache Mesos issue tracker: its 609 issues with all their events (status
changes, links, comments, assignments, ...) and the 545 earlier issues that the
duration estimation needs, with their status and resolution events. Mining it
gives the same network and durations as mining the full OCEL.

Derived from the TAWOS dataset (Tawosi, Al-Subaihin, Moussa, Sarro: A versatile
dataset of agile open source software projects, MSR 2022, Apache License 2.0)
with `code/tawos_to_ocel.py` and `code/make_sample.py MESOS 1.0.0`.
