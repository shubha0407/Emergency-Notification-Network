"""
Baseline controller B0: static shortest-path routing only, no priority queues at all.
Same code and same routes as deadlinecast_ryu.py, with the dynamic features switched off,
so the comparison only measures what those features add.

  ryu-manager --ofp-tcp-listen-port 6653 controller/baseline_plain.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deadlinecast_ryu import DeadlineCast


class BaselinePlain(DeadlineCast):
    MODE = "plain"
