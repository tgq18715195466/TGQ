import hashlib
import json
from uuid import uuid4
from scripts.migrate_legacy import migrate


def test_migration_defaults_dry_run_and_checks_hash(tmp_path):
    tenant, blob = str(uuid4()), str(uuid4())
    (tmp_path / 'source.bin').write_bytes(b'original')
    manifest = {'document_blobs': [{'id': blob, 'tenant_id': tenant, 'sha256': hashlib.sha256(b'original').hexdigest(),
                                  'data_path': 'source.bin', 'mime_type': 'text/plain', 'blob_role': 'original'}]}
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(manifest))
    report = migrate(path, tenant)
    assert report['dry_run'] and report['written'] == 0 and not report['errors']
    (tmp_path / 'source.bin').write_bytes(b'changed')
    assert migrate(path, tenant)['errors'][0]['reason'] == 'SHA256_MISMATCH'


def test_export_cannot_read_outside_root(tmp_path):
    tenant = str(uuid4())
    manifest = {'document_blobs': [{'id': str(uuid4()), 'data_path': '../secret', 'sha256': 'wrong'}]}
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(manifest))
    assert migrate(path, tenant)['errors'][0]['reason'] == 'EXPORT_PATH_ESCAPE'
