"""
mongo_extractor: libreria interna para extraer data desde MongoDB/DocumentDB
via SSH tunnel o AWS SSM port-forward, segun perfil.

API publica:
- list_profiles() -> List[str]
- extract_aggregate(profile, collection, pipeline) -> pandas.DataFrame
- run_pipeline_from_file(pipeline_file, ...) -> pandas.DataFrame
"""
from mongo_extractor.extractor import extract_aggregate, list_profiles
from mongo_extractor.pipeline_runner import run_pipeline_from_file

__all__ = ["extract_aggregate", "list_profiles", "run_pipeline_from_file"]
__version__ = "0.1.0"
