"""Ingestion graph checkpoints contain identifiers only; content remains in PostgreSQL."""
from contextlib import contextmanager
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

INGEST_STAGES = ['validate_file', 'persist_document', 'normalize_file', 'parse_with_mineru',
                 'validate_parse_quality', 'build_outline', 'create_chunks', 'generate_embeddings',
                 'sync_milvus', 'extract_entities', 'sync_neo4j', 'publish_version', 'cleanup_temp_files']

STAGE_STATUS = dict(zip(INGEST_STAGES, ['VALIDATING', 'PERSISTING', 'NORMALIZING', 'PARSING',
    'QUALITY_CHECKING', 'CHUNKING', 'CHUNKING', 'EMBEDDING', 'INDEXING', 'GRAPH_BUILDING',
    'INDEXING', 'INDEXING', 'READY']))


class IngestState(TypedDict):
    tenant_id: str
    job_id: str
    start_stage: str


def build_ingestion_graph(execute, checkpointer=None):
    graph = StateGraph(IngestState)
    for stage in INGEST_STAGES:
        def node(state, name=stage):
            if INGEST_STAGES.index(name) >= INGEST_STAGES.index(state.get('start_stage', INGEST_STAGES[0])):
                execute(name, state)
            return {}
        graph.add_node(stage, node)
    graph.add_edge(START, INGEST_STAGES[0])
    for previous, following in zip(INGEST_STAGES, INGEST_STAGES[1:]):
        graph.add_edge(previous, following)
    graph.add_edge(INGEST_STAGES[-1], END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def postgres_checkpointer(config, tenant):
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver
    from sqlalchemy.engine import make_url
    url = make_url(config.checkpoint_dsn or config.postgres_dsn).set(drivername='postgresql')
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True,
                         prepare_threshold=0, row_factory=dict_row,
                         options=f'-csearch_path=workflow -capp.tenant_id={tenant}') as conn:
        yield PostgresSaver(conn)
