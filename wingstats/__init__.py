"""wingstats - wingfoil session statistics from Garmin Connect exports.

Pipeline: parse (TCX/GPX) -> resample to 1 Hz -> detect flights, turns
(jibes/tacks), full stops -> summarise -> report (text / JSON / HTML).
"""

__version__ = "0.5.1"
