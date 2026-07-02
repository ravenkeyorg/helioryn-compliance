from helioryn.ingest.api_source.base import BaseApiSource
from helioryn.ingest.api_source.grants_gov import GrantsGovSource
from helioryn.ingest.api_source.fedreg import FederalRegisterSource
from helioryn.ingest.api_source.usa_spending import UsaSpendingSource
from helioryn.ingest.api_source.sam_assistance import SamAssistanceSource
from helioryn.ingest.api_source.sam_opportunities import SamOpportunitiesSource
from helioryn.ingest.api_source.sam_contracts import SamContractsSource
from helioryn.ingest.api_source.regulations_dot_gov import RegulationsGovSource
from helioryn.ingest.api_source.govinfo import GovInfoSource
from helioryn.ingest.api_source.congress import CongressSource
from helioryn.ingest.api_source.per_diem import PerDiemSource
from helioryn.ingest.api_source.crime_solutions import CrimeSolutionsSource
from helioryn.ingest.api_source.epa_echo import EpaEchoSource
from helioryn.ingest.api_source.oig_reports import OigReportsSource

SOURCE_MAP = {
    "grants_gov": GrantsGovSource,
    "federal_register": FederalRegisterSource,
    "usa_spending": UsaSpendingSource,
    "sam_assistance": SamAssistanceSource,
    "sam_opportunities": SamOpportunitiesSource,
    "sam_contracts": SamContractsSource,
    "regulations_gov": RegulationsGovSource,
    "govinfo": GovInfoSource,
    "congress": CongressSource,
    "per_diem": PerDiemSource,
    "crime_solutions": CrimeSolutionsSource,
    "epa_echo": EpaEchoSource,
    "oig_reports": OigReportsSource,
}


def create_api_sources(api_configs: list[dict], ingestor, store) -> list[BaseApiSource]:
    sources = []
    for cfg in api_configs:
        if not cfg.get("enabled", True):
            continue
        kind = cfg.get("type", "")
        cls = SOURCE_MAP.get(kind)
        if cls:
            sources.append(cls(cfg, ingestor, store))
        else:
            print(f"  Unknown API source type: {kind}")
    return sources
