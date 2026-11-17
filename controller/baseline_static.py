"""
Baseline controller B1: static routing + always-on DSCP priority for ANY host, no ARM, no monitoring, no reroute, no blocking.
Same code and same routes as deadlinecast_ryu.py, with the dynamic features switched off,
so the comparison only measures what those features add.

  ryu-manager --ofp-tcp-listen-port 6653 controller/baseline_static.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deadlinecast_ryu import DeadlineCast


class BaselineStatic(DeadlineCast):
    MODE = "static"
