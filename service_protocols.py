"""service_protocols.py

Service-layer contracts expressed as `typing.Protocol`.

Hard rule for the refactor: a service NEVER imports another service's
*implementation*. It depends only on the Protocol defined here. Concrete
implementations are injected through `ServiceContainer`, which imports them
lazily (inside methods), so the import cycle is broken at the source.

Layering it enforces:
    routes/*   -> depends on Protocols + container
    services/* -> depends on Protocols (NOT on each other's classes)
    data/*     -> depends on nothing above it
    core/*     -> depends on nothing above it
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

# --------------------------------------------------------------------------
# Protocols (the "interface" layer)
# --------------------------------------------------------------------------

@runtime_checkable
class SearchServiceProtocol(Protocol):
    """Free-text / ID search over pathways & genes."""

    def search(self, query: str, limit: int = 20) -> List[dict]:
        """Return ranked hits for a query (supports hsa00010 style IDs)."""
        ...


@runtime_checkable
class PathwayServiceProtocol(Protocol):
    """Fetch & enrich a single KEGG/Reactome pathway."""

    def get_pathway(self, pathway_id: str) -> dict:
        """Full pathway detail, e.g. hsa04115."""
        ...

    def get_genes(self, pathway_id: str) -> List[str]:
        """Genes participating in the pathway."""
        ...


@runtime_checkable
class DiffServiceProtocol(Protocol):
    """Differential expression analysis (DESeq2 / edgeR / limma approx)."""

    def run_diff(self, counts: dict, design: dict, method: str = "deseq2") -> dict:
        """Return volcano / MA / heatmap payload."""
        ...


@runtime_checkable
class RagServiceProtocol(Protocol):
    """RAG-backed Q&A over the knowledge base."""

    def answer(self, question: str, context_pathway_id: str | None = None) -> str:
        """Answer a question, optionally scoped to one pathway."""
        ...


# --------------------------------------------------------------------------
# Lazy container (the single place that knows concrete classes)
# --------------------------------------------------------------------------

class ServiceContainer:
    """Single-instance, lazily-resolved service registry.

    Concrete service modules are imported *inside* the property getters, never
    at module top. This is what prevents  routes/services  <->  services
    circular imports: the container module only imports `service_protocols`
    (this file), and the implementations are only touched on first access.
    """

    def __init__(self) -> None:
        self._instances: dict = {}

    def _resolve(self, key: str, import_path: str, class_name: str):
        if key not in self._instances:
            # LAZY import: inside the method, not at the top of the file.
            module = __import__(import_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            self._instances[key] = cls()
        return self._instances[key]

    @property
    def search(self) -> SearchServiceProtocol:
        return self._resolve("search", "services.search_service", "SearchService")

    @property
    def pathway(self) -> PathwayServiceProtocol:
        return self._resolve("pathway", "services.pathway_service", "PathwayService")

    @property
    def diff(self) -> DiffServiceProtocol:
        return self._resolve("diff", "services.diff_service", "DiffService")

    @property
    def rag(self) -> RagServiceProtocol:
        return self._resolve("rag", "services.rag_service", "RagService")


# Module-level singleton — import this from routes / resolvers.
services: ServiceContainer = ServiceContainer()


# --------------------------------------------------------------------------
# Example concrete implementations (live in services/*.py, NOT imported here)
# --------------------------------------------------------------------------
#
#   # services/diff_service.py
#   from service_protocols import DiffServiceProtocol, PathwayServiceProtocol
#
#   class DiffService:
#       def __init__(self) -> None:
#           # Inject the dependency via the Protocol, not a hard import.
#           self._pathway: PathwayServiceProtocol = services.pathway
#
#       def run_diff(self, counts, design, method="deseq2"):
#           ...  # uses self._pathway.get_genes(...) without importing it
#
# --------------------------------------------------------------------------
