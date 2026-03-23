# Copyright CONFLICT LLC 2026 (weareconflict.com)
"""
navegador.analysis — structural analysis tools for codebases.

Provides:
  ImpactAnalyzer  — blast-radius: what does changing X affect?
  FlowTracer      — execution flow: trace call chains from entry points
  DeadCodeDetector — find unreachable functions, classes, and files
  TestMapper      — map test functions to production code via TESTS edges
  CycleDetector   — detect circular dependencies in import and call graphs
"""

from navegador.analysis.cycles import CycleDetector
from navegador.analysis.deadcode import DeadCodeDetector
from navegador.analysis.flow import FlowTracer
from navegador.analysis.impact import ImpactAnalyzer
from navegador.analysis.testmap import TestMapper

__all__ = [
    "ImpactAnalyzer",
    "FlowTracer",
    "DeadCodeDetector",
    "TestMapper",
    "CycleDetector",
]
