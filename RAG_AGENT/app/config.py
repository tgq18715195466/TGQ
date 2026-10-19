from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    postgres_dsn: str = Field(default='postgresql+psycopg://zhanggui@localhost:5432/zhanggui', repr=False)
    postgres_pool_size: int = 10
    checkpoint_dsn: str = Field(default='', repr=False)
    redis_url: str = 'redis://localhost:6379/0'
    redis_rate_limit_enabled: bool = False
    milvus_uri: str = 'http://localhost:19530'
    milvus_token: str = Field(default='', repr=False)
    milvus_collection: str = 'knowledge'
    neo4j_uri: str = 'bolt://localhost:7687'
    neo4j_user: str = 'neo4j'
    neo4j_password: str = Field(default='', repr=False)
    mineru_base_url: str = 'http://localhost:8001'
    mineru_timeout_seconds: int = 600
    embedding_provider: str = 'openai-compatible'
    embedding_base_url: str = 'http://localhost:8002/v1'
    embedding_api_key: str = Field(default='', repr=False)
    embedding_model: str = 'BAAI/bge-m3'
    embedding_dimension: int = 1024
    embedding_batch_size: int = 32
    reranker_provider: str = 'tei'
    reranker_base_url: str = 'http://localhost:8003'
    reranker_model: str = 'BAAI/bge-reranker-v2-m3'
    reranker_timeout_seconds: float = 10
    llm_provider: str = 'openai-compatible'
    llm_base_url: str = 'http://localhost:8004/v1'
    llm_api_key: str = Field(default='', repr=False)
    llm_model: str = 'configured-by-operator'
    jwt_secret: str = Field(default='', repr=False)
    jwt_issuer: str = 'zhanggui'
    jwt_audience: str = 'zhanggui-api'
    web_search_enabled: bool = False
    mcp_search_url: str = ''
    mcp_search_token: str = Field(default='', repr=False)
    hyde_enabled: bool = False
    max_file_size_mb: int = 50
    max_document_pages: int = 500
    scanner_url: str = ''
    development_skip_scanner: bool = False
    environment: str = 'production'
    worker_poll_seconds: float = 2
    max_retries: int = 5
    vector_top_k: int = 40
    keyword_top_k: int = 30
    graph_top_k: int = 30
    graph_max_hops: int = Field(default=2, ge=1, le=2)
    rrf_k: int = 60
    rerank_candidates: int = 50
    final_context_chunks: int = 10
    max_chunks_per_document: int = 4
    evidence_min_score: float = 0.5
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    search_ef: int = 128
    query_config_version: str = 'retrieval-bm25-v2'
    chunk_config_version: str = 'chunk-v1'
    prompt_version: str = 'evidence-v1'
    rate_limit_per_minute: int = 60

    def retrieval_snapshot(self):
        names = ('vector_top_k', 'keyword_top_k', 'graph_top_k', 'graph_max_hops', 'rrf_k',
                 'rerank_candidates', 'final_context_chunks', 'max_chunks_per_document',
                 'hyde_enabled', 'web_search_enabled', 'evidence_min_score', 'query_config_version',
                 'embedding_model', 'embedding_dimension', 'reranker_model', 'llm_model', 'prompt_version')
        return {**{key: getattr(self, key) for key in names},
                'keyword_algorithm': 'bm25', 'bm25_k1': 1.2, 'bm25_b': 0.75}


@lru_cache
def settings():
    return Settings()
