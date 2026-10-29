"""Direct adapter에서도 원격 인증 전에 로컬 완료 준비와 입력 연결을 확인한다."""
import json
import hashlib
import sys
from pathlib import Path
from goal_report import ReportRuntime, ReportError, read_json, publication_snapshot_digest

try:
    request = json.load(sys.stdin)
    runtime = ReportRuntime(request)
    key = request['event_key']
    report = read_json(runtime.store.manifest_path(key))
    if (request.get('thread_id') != report.get('thread_id') or request.get('goal_id') != report.get('goal_id')
            or request.get('report_type') != report.get('report_type') or request.get('kind') != report.get('kind')
            or not (request.get('publication_request') or runtime.completion_readiness(key))):
        raise ReportError('완료 준비 신원 또는 입력 fingerprint 불일치')
    expected_route = f'/projects/{report["project"]}/{key}/'
    public = Path(request['public']).resolve()
    candidates = (runtime.store.root / 'candidates' / key).resolve()
    if request.get('report_path') != expected_route or not public.is_relative_to(candidates) or public.name != 'public':
        raise ReportError('보고서 경로 또는 후보 key 연결 불일치')
    candidate = read_json(public.parent / 'candidate.json')
    if (request.get('publication_order_digest') != candidate.get('publication_order_digest')
            or candidate.get('publication_order_digest') != publication_snapshot_digest(candidate['reports'])):
        raise ReportError('고정 발행 순번 snapshot 연결 불일치')
    if request.get('publication_request'):
        runtime.activate_requested_publication(key, request['publication_request'], request.get('publication_request_digest'), candidate)
        pending_path = runtime.store.root / 'pending-deployment.json'
        if pending_path.exists():
            pending = read_json(pending_path)
            if pending.get('key') != key or Path(pending.get('candidate', '')).resolve() != public.parent / 'candidate.json':
                raise ReportError('명시 게시 승인으로 다른 미확인 후보를 조회하지 않습니다.')
    if (candidate.get('key') != key or Path(candidate.get('public', '')).resolve() != public
            or candidate.get('input_fingerprint') != runtime.input_fingerprint(key)
            or candidate.get('report_type') != report.get('report_type') or candidate.get('kind') != report.get('kind')):
        raise ReportError('후보 완료 준비 fingerprint 불일치')
    if request.get('action') != 'status' and (request.get('candidate_digest') != candidate.get('asset_digest')
            or request.get('baseline_digest') != candidate.get('baseline', {}).get('asset_digest')
            or request.get('previous_assets') != candidate.get('baseline', {}).get('assets')
            or request.get('changed_paths') != candidate.get('changed_paths')
            or request.get('navigation_migrations', []) != candidate.get('navigation_migrations', [])):
        raise ReportError('후보 게시 보호 입력 불일치')
    if request.get('action') == 'status' and (request.get('expected_asset_digest') != candidate.get('asset_digest')
            or request.get('candidate_digest') != candidate.get('asset_digest')):
        raise ReportError('후보 원격 조회 해시 연결 불일치')
    expected = {asset['path']: asset for asset in candidate['assets']}
    actual = {}
    for path in public.rglob('*'):
        if path.is_symlink():
            raise ReportError('후보에 symlink가 있습니다.')
        if path.is_file():
            data = path.read_bytes()
            actual[path.relative_to(public).as_posix()] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if set(actual) != set(expected) or any(actual[name] != {field: asset[field] for field in ('bytes', 'sha256')} for name, asset in expected.items()):
        raise ReportError('후보 자산 바이트가 준비 기록과 다릅니다.')
    print(json.dumps({'eligible': True}))
except (OSError, ValueError, TypeError, KeyError, ReportError):
    print(json.dumps({'eligible': False}))
    sys.exit(1)
