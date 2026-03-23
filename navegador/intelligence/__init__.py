"""
Intelligence layer — semantic search, community detection, NLP queries, and doc generation.

  SemanticSearch  — embedding-based similarity search over graph nodes
  CommunityDetector — label-propagation community detection over the graph
  NLPEngine       — natural language queries, community naming, doc generation
  DocGenerator    — markdown documentation from graph context
"""

from navegador.intelligence.community import Community, CommunityDetector
from navegador.intelligence.docgen import DocGenerator
from navegador.intelligence.nlp import NLPEngine
from navegador.intelligence.search import SemanticSearch

__all__ = [
    "SemanticSearch",
    "CommunityDetector",
    "Community",
    "NLPEngine",
    "DocGenerator",
]
