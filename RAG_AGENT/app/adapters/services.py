from functools import cached_property
from .models import ModelClient, extract_graph
from .stores import GraphStore, VectorStore


class Services:
    def __init__(self, config):
        self.config = config

    @cached_property
    def models(self):
        return ModelClient(self.config)

    @cached_property
    def vectors(self):
        return VectorStore(self.config)

    @cached_property
    def graph(self):
        return GraphStore(self.config)

    def embed(self, texts):
        return self.models.embed(texts)

    def extract_graph(self, chunks):
        return extract_graph(chunks)

    def sync_vectors(self, tenant, version_id, chunks, documents, outbox_id, embeddings=None):
        vectors = embeddings if embeddings is not None else self.embed([x.embedding_text for x in chunks])
        self.vectors.sync(tenant, version_id, chunks, documents, vectors)

    def sync_graph(self, tenant, version_id, chunks, documents, outbox_id, graph=None):
        self.graph.sync(tenant, version_id, chunks, documents, graph or self.extract_graph(chunks))

    def delete_vectors(self, tenant, version_id, outbox_id):
        self.vectors.delete(tenant, version_id)

    def delete_graph(self, tenant, version_id, outbox_id):
        self.graph.delete(tenant, version_id)

    def vector_ids(self, tenant, version_id=None):
        return self.vectors.ids(tenant, version_id)

    def graph_ids(self, tenant, version_id=None):
        return self.graph.ids(tenant, version_id)

    def close(self):
        for key in ('models', 'vectors', 'graph'):
            if key in self.__dict__:
                self.__dict__[key].close()
