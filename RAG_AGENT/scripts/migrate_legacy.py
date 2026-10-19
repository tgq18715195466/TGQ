"""Import an offline export into logical PostgreSQL tables. Defaults to dry-run.

Never connects to or deletes old infrastructure. Exporters run separately from the app.
See docs/migration_guide.md for the manifest contract and legacy mapping.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import DateTime, select
from app.db import transaction
from app.models import Base

ORDER = ['tenants', 'users', 'roles', 'user_roles', 'knowledge_bases', 'kb_members', 'document_blobs',
         'documents', 'document_versions', 'parsed_pages', 'document_assets', 'parsed_blocks', 'chunks',
         'ingest_jobs', 'job_steps', 'chat_sessions', 'query_runs', 'messages', 'retrieval_candidates']


def inspect_export(manifest, root, tenant):
    root = Path(root).resolve()
    checked, errors = [], []
    for table_name in ORDER:
        table = Base.metadata.tables[table_name]
        for source in manifest.get(table_name, []):
            try:
                row = dict(source)
                if table_name == 'tenants':
                    if str(UUID(row['id'])) != tenant: raise ValueError('TENANT_MISMATCH')
                else:
                    if row.get('tenant_id', tenant) != tenant: raise ValueError('TENANT_MISMATCH')
                    row['tenant_id'] = tenant
                if 'data_path' in row:
                    path = (root / row.pop('data_path')).resolve()
                    if not path.is_relative_to(root): raise ValueError('EXPORT_PATH_ESCAPE')
                    row['data'] = path.read_bytes()
                    actual = hashlib.sha256(row['data']).hexdigest()
                    if row.get('sha256') != actual: raise ValueError('SHA256_MISMATCH')
                    if table_name == 'document_blobs': row['size_bytes'] = len(row['data'])
                unknown = set(row) - set(table.c.keys())
                if unknown: raise ValueError('UNKNOWN_FIELDS')
                for name, value in list(row.items()):
                    if isinstance(table.c[name].type, DateTime) and isinstance(value, str):
                        row[name] = datetime.fromisoformat(value)
                # Imported document versions must be rebuilt before becoming query-visible.
                if table_name == 'documents':
                    row['current_version_id'], row['status'] = None, 'QUEUED'
                if table_name == 'document_versions': row['status'] = 'QUEUED'
                if table_name == 'ingest_jobs':
                    row.update(status='QUEUED', current_stage='validate_file', retry_count=0, cancel_requested=False)
                checked.append((table_name, row))
            except Exception as exc:
                errors.append({'table': table_name, 'id': source.get('id'), 'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    return checked, errors


def migrate(path, tenant, commit=False):
    path = Path(path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    rows, errors = inspect_export(manifest, path.parent, tenant)
    report = {'dry_run': not commit, 'validated': len(rows), 'written': 0, 'existing': 0, 'errors': errors,
              'counts': {name: sum(table == name for table, row in rows) for name in ORDER},
              'vector_count': None, 'graph_node_count': None,
              'reindex_required': True, 'source_data_deleted': False}
    if not commit or errors: return report
    for name, row in rows:
        try:
            with transaction(tenant) as db:
                table = Base.metadata.tables[name]
                existing = db.execute(select(table.c.id).where(table.c.id == row['id'])).first()
                if existing:
                    report['existing'] += 1
                else:
                    db.execute(table.insert().values(**row)); report['written'] += 1
        except Exception as exc:
            report['errors'].append({'table': name, 'id': row.get('id'), 'reason': type(exc).__name__})
            break  # replay resumes safely using stable primary IDs
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest'); parser.add_argument('--tenant', required=True, type=UUID)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dry-run', action='store_true'); group.add_argument('--commit', action='store_true')
    parser.add_argument('--report', default='migration-report.json')
    args = parser.parse_args()
    report = migrate(args.manifest, str(args.tenant), args.commit)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'errors'}, ensure_ascii=False))
    raise SystemExit(bool(report['errors']))


if __name__ == '__main__': main()
