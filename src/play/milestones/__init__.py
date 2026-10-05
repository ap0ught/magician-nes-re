"""One script per milestone.

    m1_first_town.py   power-on -> title -> map screen -> the first town

A milestone is the same contract every time: run the route from power-on, take a
checkpoint before every segment, save the input log, replay that log from
power-on in a FRESH emulator, and print the completion line only if the replay
produced the same RAM fingerprint AND every route assertion held.
"""
