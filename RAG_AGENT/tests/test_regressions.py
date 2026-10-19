from uuid import uuid4
import base64

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ingestion import ParsedBlock, ChunkConfig, IngestionError, adapt_mineru_response, create_chunks
from app.models import Base, DocumentAsset


def test_mineru_numbered_page_and_code_accepted_without_visual_guessing():
    raw = {'results': {'doc': {'content_list': [
        {'type': 'page_number', 'text': '1', 'page_idx': 0},
        {'type': 'code', 'code_body': 'print(1)', 'page_idx': 0},
        {'type': 'chart', 'image_caption': ['图一'], 'image_description': 'UNTRUSTED VISUAL GUESS', 'page_idx': 0}],
        'middle_json': {'pdf_info': [{}]}}}}
    parsed = adapt_mineru_response(raw, str(uuid4()))
    assert 'print(1)' in ''.join(x.text for x in parsed.blocks)
    assert 'UNTRUSTED' not in ''.join(x.text for x in parsed.blocks)


def test_basename_assets_match_parser_paths():
    from app.ingest_runner import IngestRunner
    from app.config import Settings
    tenant, version = str(uuid4()), str(uuid4())
    raw = {'results': {'doc': {'content_list': [{'type': 'image', 'img_path': 'images/example.png', 'page_idx': 0}],
        'middle_json': {'pdf_info': [{}]}, 'images': {'example.png': base64.b64encode(b'fixture-bytes').decode()}}}}
    parsed = adapt_mineru_response(raw, version)
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with Session(engine) as db:
        IngestRunner(Settings(), None).persist_assets(db, tenant, version, parsed)
        db.flush()
        assert db.scalar(select(DocumentAsset)).id == parsed.blocks[0].asset_id


def test_large_formula_is_never_cut_into_invalid_latex():
    formula = r'\frac{' + 'a'*100 + '}{b}'
    blocks = [ParsedBlock(str(uuid4()), 1, 'formula', text=formula, formula_latex=formula)]
    config = ChunkConfig(target_tokens=30, max_tokens=50, overlap_tokens=0, minimum_merge_tokens=0)
    with pytest.raises(IngestionError, match='CHUNK_TOO_LARGE'):
        create_chunks(blocks, str(uuid4()), str(uuid4()), 'doc', config)


def test_small_formula_keeps_type():
    blocks = [ParsedBlock(str(uuid4()), 1, 'formula', text='x+y', formula_latex='x+y')]
    assert create_chunks(blocks, str(uuid4()), str(uuid4()), 'doc')[0].block_type == 'formula'
