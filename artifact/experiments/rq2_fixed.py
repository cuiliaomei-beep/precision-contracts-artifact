"""Compatibility import: both RQ2 entry points execute the repaired implementation."""
from .rq2 import RQ2Experiment, TopologyExperiment, check_final_map
__all__ = ['RQ2Experiment', 'TopologyExperiment', 'check_final_map']
