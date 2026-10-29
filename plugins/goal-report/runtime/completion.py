"""완료 전 보고서 시도와 완료 답변을 한 번에 준비한다."""
import hashlib
import json
import re
import time
from pathlib import Path

from goal_report import (FileLock, ReportError, ReportRuntime, atomic_json,
                         event_key, read_json, reject_credentials, submitted_goal_identity)


def prose(text):
    text = re.sub(r'(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$', '', text)
    text = re.sub(r'<!--.*?-->|`[^`]*`', '', text, flags=re.S)
    return re.sub(r'(?m)^(?: {4}|\t).*$', '', text)


def completion_path(root, key):
    if not re.fullmatch('[0-9a-f]{24}', key):
        raise ReportError('잘못된 완료 보고서 key입니다.')
    return Path(root) / 'completions' / (key + '.json')


def check_answer(config, record, answer, completed_urls=()):
    """초안과 Stop의 실제 본문에 동일한 검사를 적용한다. 외부 호출 없음."""
    if event_key(record['thread_id'], record['goal_id']) != record['key']:
        raise ReportError('완료 기록의 식별자가 일치하지 않습니다.')
    if record['report_status'] == 'verified':
        ReportRuntime(config).check_final(record['key'], record['thread_id'], record['goal_id'], answer)
        links = re.findall(r'(?<![!\\])\[[^\]\n]+\]\((https://[^\s)]+)\)', prose(answer))
        prefix = config['base_url'].rstrip('/') + '/projects/'
        allowed = set(completed_urls) | {record['url']}
        if any(link.startswith(prefix) and link not in allowed for link in links):
            raise ReportError('완료 답변에 다른 보고서 링크가 함께 있습니다. 이 turn에서 완료한 goal 링크만 사용하세요.')
    elif record['report_status'] == 'failed':
        if record['final_response_markdown'] not in prose(answer):
            raise ReportError('완료 답변에 보고서 실패 상태와 원인이 없습니다.')
    elif record['report_status'] == 'excluded':
        if record.get('url') or record.get('verified') or record.get('deployed'):
            raise ReportError('게시 대상 제외 기록에 배포 성공 정보가 있습니다.')
    else:
        raise ReportError('알 수 없는 보고서 상태입니다.')
    return hashlib.sha256(answer.encode('utf-8')).hexdigest()


def prepare_completion(config, manifest, draft, output, deployer=None):
    manifest, draft, output = map(Path, (manifest, draft, output))
    if output.resolve() in {manifest.resolve(), draft.resolve()}:
        raise ReportError('출력 답변은 입력 manifest·초안과 다른 파일이어야 합니다.')
    raw = read_json(manifest)
    if not isinstance(raw, dict):
        raise ReportError('manifest는 JSON 객체여야 합니다.')
    # Missing or placeholder IDs cannot be turned into a durable fake goal.
    for field in ('thread_id', 'goal_id'):
        if not isinstance(raw.get(field), str) or not raw[field].strip() or raw[field].upper().startswith('REPLACE'):
            raise ReportError('실제 thread_id와 goal_id가 필요합니다. ID를 추정하지 마세요.')
    body = draft.read_text(encoding='utf-8-sig').strip()
    key = event_key(raw['thread_id'], raw['goal_id'])
    runtime = ReportRuntime(config, deployer)
    record = {'schema_version': 1, 'key': key, 'thread_id': raw['thread_id'],
              'report_type': raw.get('report_type', 'other'),
              'goal_id': raw['goal_id'], 'goal_completion_allowed': True,
              'attempted_at_ms': int(time.time() * 1000)}
    # Separate lock from store.lock: serialize the whole attempt without nested locking.
    with FileLock(runtime.store.root / 'completion-locks' / (key + '.lock')):
        try:
            identity = submitted_goal_identity(raw, runtime.store.root)
            if identity:
                record['goal_created_at_ms'] = identity['created_at_ms']
            key = runtime.store.submit(manifest)
            runtime.store.render(key)
            runtime.seal_completion(key)
            verified_identity = runtime.store.manifest_path(key).with_name('identity.json')
            if verified_identity.exists():
                record['goal_created_at_ms'] = read_json(verified_identity)['created_at_ms']
            result = runtime.retry(key)
            record.update(result, report_status=result.get('report_status', 'verified'))
        except (ReportError, ValueError, OSError) as error:
            reason = runtime.failure_reason(error)
            # Exception text may contain an upstream path or supplied value; do not publish secrets.
            try:
                reject_credentials(reason)
            except ReportError:
                reason = '민감 정보 형식이 포함된 입력을 거부했습니다. 선택한 근거를 확인하세요.'
            local = runtime.store.root / 'previews' / 'projects' / str(raw.get('project', '')) / key / 'index.html'
            outcome = getattr(error, 'outcome', {})
            record.update(report_status='failed', verified=False, deployed=outcome.get('deployed', False),
                          deployment_status=outcome.get('deployment_status', 'failed'), local_generated=local.is_file(), error=reason,
                          final_response_markdown='보고서 생성·배포·검증 실패: ' + reason)
        if record['report_status'] == 'verified':
            status = '보고서 배포·검증 완료. ' + record['final_response_markdown']
            if record.get('content_status') == 'not-run':
                status += ' 인증 후 본문·영상 검사는 미실행입니다.'
            if record.get('protection_scope') == 'unresolved':
                status += ' 현재 보고서의 비로그인 차단은 확인했으나 Access 정책 전체 범위는 미확정입니다.'
        elif record['report_status'] == 'failed':
            status = record['final_response_markdown']
        else:
            status = record.get('final_response_markdown', '')
        answer = body + ('\n\n' + status if status else '') + '\n'
        record['draft_sha256'] = check_answer(config, record, answer)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(answer, encoding='utf-8', newline='\n')
        record.update(answer_check='passed', final_answer_path=str(output.resolve()))
        atomic_json(completion_path(runtime.store.root, key), record)
        return record
