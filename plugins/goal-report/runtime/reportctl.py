"""보고서 패키지의 단일 CLI. Telegram 전송 기능은 제공하지 않는다."""
import argparse
import json
from pathlib import Path

from goal_report import FileLock, ReportError, ReportRuntime, ReportStore, load_config, read_json
from completion import check_answer, completion_path, prepare_completion


def prepare_report(config, manifest):
    """보고서를 생성하고 기존 완료 준비 상태에 따라 로컬 보관 또는 게시를 복구한다."""
    raw = read_json(manifest)
    if not isinstance(raw, dict):
        raise ReportError('manifest는 JSON 객체여야 합니다.')
    runtime = ReportRuntime(config)
    key = runtime.store.submit(manifest)
    try:
        result = runtime.retry(key)
        return dict(result, report_status=result.get('report_status', 'verified'))
    except (ReportError, OSError, ValueError) as error:
        return dict(runtime.failure_result(key, error), report_status='failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description='구현 완료 보고서 생성·게시 및 goal 완료 준비·복구')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--root', type=Path, help='로컬 생성 전용 저장 경로')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('rebuild-local-catalogue', help='확정 게시 원본으로 로컬 프로젝트 목록만 재구성')
    general = sub.add_parser('prepare-report', help='보고서 로컬 생성; goal 최종화는 prepare-completion 사용')
    general.add_argument('--manifest', type=Path, required=True)
    requested = sub.add_parser('publish-requested', help='명시 사용자 지시를 현재 구현 보고서·후보에 연결하여 게시')
    requested.add_argument('--manifest', type=Path, required=True)
    requested.add_argument('--request', type=Path, required=True)
    prepare = sub.add_parser('prepare-completion', help='goal 완료 전에 생성·배포 결과와 답변을 준비')
    prepare.add_argument('--manifest', type=Path, required=True)
    prepare.add_argument('--draft', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    submit = sub.add_parser('submit', help='진단: 로컬 HTML만 생성')
    submit.add_argument('--manifest', type=Path, required=True)
    for name in ('publish', 'retry', 'render'):
        sub.add_parser(name).add_argument('--key', required=True)
    check = sub.add_parser('check-final', help='진단: 현재 완료 결과와 답변 검사')
    for flag in ('key', 'thread-id', 'goal-id'):
        check.add_argument('--' + flag, required=True)
    check.add_argument('--draft', type=Path, required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    root = args.root or (Path(config['root']) if config else Path.home() / '.codex' / 'reports')
    store = ReportStore(root)
    def render_local(key):
        if config and root.resolve() == Path(config['root']).resolve():
            return ReportRuntime(config).render_local(key)
        return store.render(key)
    if args.action == 'submit':
        key = store.submit(args.manifest)
        with FileLock(root / 'store.lock'):
            path = render_local(key)
            store.preflight()
        report = read_json(store.manifest_path(key))
        identity = store.manifest_path(key).with_name('identity.json').exists()
        print(json.dumps({'key': key, 'thread_id': report.get('thread_id'), 'goal_id': report.get('goal_id'),
              'html': str(path), 'evidence_missing': bool(report.get('evidence_missing')),
              'goal_identity_ready': identity, 'deployed': False}, ensure_ascii=False))
        return 2 if report.get('evidence_missing') or (config and report.get('goal_id') and not identity) else 0
    if args.action == 'render':
        with FileLock(root / 'store.lock'):
            path = render_local(args.key)
        print(json.dumps({'html': str(path), 'deployed': False}, ensure_ascii=False))
        return 0
    if not config or root.resolve() != Path(config['root']).resolve():
        raise ReportError('활성화된 패키지 설정과 저장 경로가 필요합니다.')
    runtime = ReportRuntime(config)
    if args.action == 'publish-requested':
        try:
            result = runtime.publish_requested(args.manifest, args.request)
        except ReportError as error:
            print(json.dumps(dict(getattr(error, 'outcome', {}), report_status='failed', verified=False, error=str(error)), ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.action == 'rebuild-local-catalogue':
        with FileLock(root / 'store.lock'):
            result = runtime.rebuild_local_catalogue()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.action == 'prepare-report':
        result = prepare_report(config, args.manifest)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['report_status'] in {'verified', 'excluded'} else 1
    if args.action == 'prepare-completion':
        result = prepare_completion(config, args.manifest, args.draft, args.output)
    elif args.action == 'check-final':
        try:
            path = completion_path(root, args.key)
            if path.exists():
                result = read_json(path)
                if result['thread_id'] != args.thread_id or result['goal_id'] != args.goal_id:
                    raise ReportError('답변 대상 thread/goal과 보고서가 다릅니다.')
                digest = check_answer(config, result, args.draft.read_text(encoding='utf-8'))
                result = dict(result, final_check='passed', draft_sha256=digest)
            else:
                result = runtime.check_final(args.key, args.thread_id, args.goal_id, args.draft.read_text(encoding='utf-8'))
        except (ReportError, OSError, ValueError) as error:
            print(json.dumps({'final_check': 'failed', 'error': runtime.failure_reason(error)}, ensure_ascii=False))
            return 2
    else:
        try:
            result = runtime.retry(args.key)
        except ReportError as error:
            print(json.dumps(runtime.failure_result(args.key, error), ensure_ascii=False))
            return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ReportError, OSError, ValueError) as error:
        print(json.dumps({'error': str(error), 'report_status': 'failed', 'verified': False}, ensure_ascii=False))
        raise SystemExit(1)
