import hashlib
import json
import re
from uuid import UUID


def canonical(value):
    return str(UUID(str(value)))


def vector_filter(tenant, kbs, versions, user):
    tenant, user = canonical(tenant), canonical(user)
    kbs, versions = [canonical(x) for x in kbs], [canonical(x) for x in versions]
    return (f'tenant_id == {json.dumps(tenant)} and kb_id in {json.dumps(kbs)} '
            f'and version_id in {json.dumps(versions)} and '
            f'(array_contains(acl_scope, "kb") or array_contains(acl_scope, {json.dumps(user)}))')


class VectorStore:
    def __init__(self, config):
        from pymilvus import MilvusClient
        self.config = config
        digest = hashlib.sha256(config.embedding_model.encode()).hexdigest()[:12]
        self.collection = f'{config.milvus_collection}_{digest}_{config.embedding_dimension}'
        self.client = MilvusClient(uri=config.milvus_uri, token=config.milvus_token, timeout=20)

    def ensure(self):
        from pymilvus import DataType
        if self.client.has_collection(self.collection):
            return
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        for name in ['chunk_id', 'tenant_id', 'kb_id', 'document_id', 'version_id', 'content_type', 'model_version']:
            schema.add_field(name, DataType.VARCHAR, is_primary=name == 'chunk_id', max_length=200)
        schema.add_field('embedding', DataType.FLOAT_VECTOR, dim=self.config.embedding_dimension)
        for name in ['page_start', 'page_end']:
            schema.add_field(name, DataType.INT64)
        schema.add_field('acl_scope', DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=1024, max_length=64)
        indexes = self.client.prepare_index_params()
        indexes.add_index('embedding', index_type='HNSW', metric_type='COSINE',
                          params={'M': self.config.hnsw_m, 'efConstruction': self.config.hnsw_ef_construction})
        self.client.create_collection(self.collection, schema=schema, index_params=indexes,
                                      consistency_level='Strong')

    def sync(self, tenant, version, chunks, documents, embeddings):
        self.ensure()
        docs = {x.id: x for x in documents}
        if len(chunks) != len(embeddings):
            raise ValueError('Embedding count mismatch')
        rows = []
        for chunk, vector in zip(chunks, embeddings):
            if chunk.tenant_id != tenant or chunk.version_id != version:
                raise PermissionError('FORBIDDEN_SCOPE')
            doc = docs[chunk.document_id]
            rows.append({'chunk_id': chunk.id, 'embedding': vector, 'tenant_id': tenant, 'kb_id': chunk.kb_id,
                'document_id': chunk.document_id, 'version_id': version, 'content_type': chunk.content_type,
                'page_start': chunk.page_start, 'page_end': chunk.page_end,
                'acl_scope': doc.acl_jsonb.get('user_ids') or ['kb'], 'model_version': self.config.embedding_model})
        for offset in range(0, len(rows), 100):
            self.client.upsert(self.collection, rows[offset:offset + 100])

    def search(self, embedding, tenant, kbs, versions, user, limit):
        if not versions:
            return []
        result = self.client.search(self.collection, [embedding],
            filter=vector_filter(tenant, kbs, versions, user), limit=limit, output_fields=['chunk_id'],
            search_params={'metric_type': 'COSINE', 'params': {'ef': max(limit, self.config.search_ef)}})
        return [(str(hit['entity']['chunk_id']), float(hit['distance'])) for hit in result[0]]

    def delete(self, tenant, version):
        for collection in self.owned_collections():
            self.client.delete(collection, filter=f'tenant_id == "{canonical(tenant)}" and version_id == "{canonical(version)}"')

    def owned_collections(self):
        pattern = re.compile(re.escape(self.config.milvus_collection) + r'_[a-f0-9]{12}_[0-9]+$')
        return [name for name in self.client.list_collections() if pattern.fullmatch(name)]

    def ids(self, tenant, version=None):
        query = f'tenant_id == "{canonical(tenant)}"'
        if version:
            query += f' and version_id == "{canonical(version)}"'
        result = set()
        collections = self.owned_collections()
        # A version's readiness concerns the queried model, not a union hiding missing current vectors.
        if version is not None:
            collections = [self.collection] if self.collection in collections else []
        for collection in collections:
            iterator = self.client.query_iterator(collection, filter=query, output_fields=['chunk_id'], batch_size=1000)
            try:
                while batch := iterator.next():
                    result.update(row['chunk_id'] for row in batch)
            finally:
                iterator.close()
        return result

    def purge(self, tenant, chunk_ids):
        for collection in self.owned_collections():
            for offset in range(0, len(chunk_ids), 100):
                ids = [canonical(x) for x in chunk_ids[offset:offset+100]]
                self.client.delete(collection, filter=f'tenant_id == "{canonical(tenant)}" and chunk_id in {json.dumps(ids)}')

    def close(self):
        self.client.close()


class GraphStore:
    def __init__(self, config):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))

    def run(self, query, **params):
        with self.driver.session() as session:
            return list(session.run(query, **params))

    def ensure(self):
        for label in ['Document', 'Chunk', 'Entity', 'Alias']:
            self.run(f'CREATE CONSTRAINT {label.lower()}_identity IF NOT EXISTS FOR (n:{label}) REQUIRE (n.tenant_id,n.id) IS UNIQUE')

    def sync(self, tenant, version, chunks, documents, graph):
        self.ensure()
        docs = {x.id: x for x in documents}
        for chunk in chunks:
            if chunk.tenant_id != tenant or chunk.version_id != version:
                raise PermissionError('FORBIDDEN_SCOPE')
            self.run('''MERGE (d:Document {tenant_id:$tenant,id:$doc}) SET d.kb_id=$kb
                MERGE (c:Chunk {tenant_id:$tenant,id:$chunk})
                SET c.kb_id=$kb,c.version_id=$version,c.document_id=$doc,c.chunk_no=$number,c.acl_scope=$acl
                MERGE (c)-[r:PART_OF]->(d)
                SET r.tenant_id=$tenant,r.kb_id=$kb,r.source_chunk_id=$chunk,r.extractor_version='explicit-v1',
                    r.confidence=1.0,r.created_at=coalesce(r.created_at,datetime())''',
                tenant=tenant, doc=chunk.document_id, kb=chunk.kb_id, chunk=chunk.id,
                version=version, number=chunk.chunk_no, acl=docs[chunk.document_id].acl_jsonb.get('user_ids') or ['kb'])
        by_id = {c.id: c for c in chunks}
        for entity in graph['entities']:
            source = by_id[entity['source_chunk_id']]
            eid = hashlib.sha256(f'{source.kb_id}:{entity["name"].casefold()}'.encode()).hexdigest()
            self.run('''MATCH (c:Chunk {tenant_id:$tenant,id:$chunk})
                MERGE (e:Entity {tenant_id:$tenant,id:$eid}) SET e.name=$name,e.kind=$kind,e.kb_id=$kb
                MERGE (c)-[r:MENTIONS]->(e)
                SET r.tenant_id=$tenant,r.kb_id=$kb,r.source_chunk_id=$chunk,r.version_id=$version,
                r.extractor_version='explicit-v1',r.confidence=1.0,r.created_at=coalesce(r.created_at,datetime())
                MERGE (e)-[s:SUPPORTED_BY {source_chunk_id:$chunk}]->(c)
                SET s.tenant_id=$tenant,s.kb_id=$kb,s.version_id=$version,
                s.extractor_version='explicit-v1',s.confidence=1.0,s.created_at=coalesce(s.created_at,datetime())''',
                tenant=tenant, chunk=source.id, kb=source.kb_id, version=version,
                eid=eid, name=entity['name'], kind=entity['type'])
            for alias in entity['aliases']:
                aid = hashlib.sha256(f'{source.kb_id}:{alias.casefold()}'.encode()).hexdigest()
                self.run('''MATCH (e:Entity {tenant_id:$tenant,id:$eid})
                    MERGE (a:Alias {tenant_id:$tenant,id:$aid}) SET a.name=$alias,a.kb_id=$kb
                    MERGE (a)-[r:RELATES_TO {source_chunk_id:$chunk,kind:'alias'}]->(e)
                    SET r.tenant_id=$tenant,r.kb_id=$kb,r.version_id=$version,r.extractor_version='explicit-v1',
                    r.confidence=1.0,r.created_at=coalesce(r.created_at,datetime())''',
                    tenant=tenant, eid=eid, aid=aid, alias=alias, kb=source.kb_id, chunk=source.id, version=version)
        for rel in graph['relations']:
            source = by_id[rel['source_chunk_id']]
            self.run('''MATCH (a:Entity {tenant_id:$tenant,kb_id:$kb,name:$left}),
                (b:Entity {tenant_id:$tenant,kb_id:$kb,name:$right})
                MERGE (a)-[r:RELATES_TO {source_chunk_id:$chunk,kind:$kind}]->(b)
                SET r.tenant_id=$tenant,r.kb_id=$kb,r.version_id=$version,r.extractor_version='explicit-v1',
                    r.confidence=1.0,r.created_at=coalesce(r.created_at,datetime())''',
                tenant=tenant, kb=source.kb_id, left=rel['source'], right=rel['target'],
                chunk=source.id, kind=rel['type'], version=version)
        for previous, following in zip(chunks, chunks[1:]):
            self.run('''MATCH (a:Chunk {tenant_id:$tenant,id:$a}), (b:Chunk {tenant_id:$tenant,id:$b})
                MERGE (a)-[r:NEXT_CHUNK]->(b) SET r.tenant_id=$tenant,r.kb_id=$kb,
                r.source_chunk_id=$a,r.version_id=$version,r.extractor_version='explicit-v1',r.confidence=1.0,
                r.created_at=coalesce(r.created_at,datetime())''',
                tenant=tenant, a=previous.id, b=following.id, kb=previous.kb_id, version=version)

    def search(self, question, tenant, kbs, versions, user, limit, hops=2):
        if hops not in {1, 2}:
            raise ValueError('Graph traversal limited to two hops')
        params = dict(tenant=canonical(tenant), kbs=[canonical(x) for x in kbs],
                      versions=[canonical(x) for x in versions], user=canonical(user), question=question, limit=limit)
        # Every traversed relationship has provenance and must belong to an authorized current version.
        records = self.run(f'''MATCH (e) WHERE (e:Entity OR e:Alias) AND e.tenant_id=$tenant
            AND e.kb_id IN $kbs AND toLower($question) CONTAINS toLower(e.name)
            MATCH p=(e)-[:RELATES_TO*0..{hops}]-(target)
            WHERE ALL(n IN nodes(p) WHERE n.tenant_id=$tenant AND n.kb_id IN $kbs)
              AND ALL(r IN relationships(p) WHERE r.tenant_id=$tenant AND r.kb_id IN $kbs
                AND r.version_id IN $versions AND r.source_chunk_id IS NOT NULL
                AND EXISTS {{ MATCH (s:Chunk {{tenant_id:$tenant,id:r.source_chunk_id}})
                    WHERE s.version_id IN $versions AND ('kb' IN s.acl_scope OR $user IN s.acl_scope) }})
            MATCH (target)-[support:SUPPORTED_BY]->(c:Chunk)
            WHERE c.tenant_id=$tenant AND c.kb_id IN $kbs AND c.version_id IN $versions
              AND ('kb' IN c.acl_scope OR $user IN c.acl_scope)
              AND support.tenant_id=$tenant AND support.source_chunk_id=c.id
            RETURN DISTINCT c.id AS chunk_id, 1.0/(1+length(p)) AS score
            ORDER BY score DESC LIMIT $limit''', **params)
        return [(r['chunk_id'], float(r['score'])) for r in records]

    def delete(self, tenant, version):
        # Remove provenance edges before their Chunk nodes so no dangling relationship survives.
        self.run('MATCH ()-[r]->() WHERE r.tenant_id=$tenant AND r.version_id=$version DELETE r',
                 tenant=canonical(tenant), version=canonical(version))
        self.run('MATCH (c:Chunk {tenant_id:$tenant,version_id:$version}) DETACH DELETE c', tenant=tenant, version=version)
        self.run('''MATCH (n) WHERE n.tenant_id=$tenant AND (n:Entity OR n:Alias OR n:Document)
                    AND NOT (n)--() DELETE n''', tenant=tenant)

    def ids(self, tenant, version=None):
        result = self.run('''MATCH (c:Chunk {tenant_id:$tenant})
            WHERE $version IS NULL OR c.version_id=$version RETURN c.id AS id''', tenant=canonical(tenant), version=version)
        return {row['id'] for row in result}

    def close(self):
        self.driver.close()

    def purge(self, tenant, chunk_ids):
        self.run('MATCH ()-[r]->() WHERE r.tenant_id=$tenant AND r.source_chunk_id IN $ids DELETE r',
                 tenant=canonical(tenant), ids=[canonical(x) for x in chunk_ids])
        self.run('MATCH (c:Chunk {tenant_id:$tenant}) WHERE c.id IN $ids DETACH DELETE c',
                 tenant=tenant, ids=chunk_ids)
