"""완료 전 준비 확인과 완료 후 답변 검사. 배포·알림·goal 변경은 하지 않는다."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from contextlib import closing

from completion import check_answer, completion_path
from goal_report import FileLock, ReportError, ReportRuntime, atomic_json, event_key, load_config, read_json


def is_subagent(event):
    if event.get('agent_type') or event.get('parent_thread_id'):
        return True
    transcript = event.get('transcript_path')
    if transcript:
        try:
            with Path(transcript).open(encoding='utf-8') as stream:
                for _ in range(20):
                    line = stream.readline(100000)
                    if not line:
                        break
                    row = json.loads(line)
                    if row.get('type') == 'session_meta':
                        source = row.get('payload', {}).get('source')
                        return isinstance(source, dict) and 'subagent' in source
        except (OSError, ValueError):
            pass
    return False


def current_goal(config, thread):
    candidates = [Path(config['goal_database'])] if config.get('goal_database') else [
        Path(config['root']).parent / 'goals_1.sqlite',
        Path(config['root']).parent / 'sqlite' / 'goals_1.sqlite']
    matches = set()
    for path in candidates:
        if not path.is_file():
            continue
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for goal, created in db.execute('SELECT goal_id,created_at_ms FROM thread_goals WHERE thread_id=?', (thread,)):
                matches.add((goal, created))
    if len(matches) != 1:
        raise ReportError('현재 goal을 유일하게 확인할 수 없습니다. 실제 ID로 완료 준비 결과를 확인하세요.')
    return next(iter(matches))


def goal_results(value):
    if isinstance(value, str):
        try:
            yield from goal_results(json.loads(value))
        except ValueError:
            return
    elif isinstance(value, list):
        for item in value:
            yield from goal_results(item)
    elif isinstance(value, dict):
        if value.get('status') == 'complete':
            yield value
        else:
            for item in value.values():
                if isinstance(item, (dict, list, str)):
                    yield from goal_results(item)


def turn_path(root, event):
    identity = str(event['session_id']) + '\x1f' + str(event['turn_id'])
    return Path(root) / 'turns' / (hashlib.sha256(identity.encode()).hexdigest()[:24] + '.json')


def handle(event, config):
    if not config or is_subagent(event):
        return {}
    name = event.get('hook_event_name')
    if name == 'SessionStart':
        skill = Path(__file__).parents[1] / 'skills' / 'goal-completion-report' / 'SKILL.md'
        return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext':
                '구현 완료 보고서 요청 또는 메인 구현 goal 완료에만 보고서 패키지를 적용하세요. '
                '명세서·구현 계획서·검증 보고서·조사 문서는 Cloudflare 게시 대상이 아니며 '
                '문서 goal 완료에 배포 준비나 링크를 요구하지 않습니다.\n'
                + str(skill) + '\n' + skill.read_text(encoding='utf-8')}}
    if not event.get('session_id') or not event.get('turn_id'):
        return {}
    if name in {'PreToolUse', 'PostToolUse'}:
        if not re.search(r'(?:^|[.:_])update_goal$', event.get('tool_name', '')):
            return {}
        if event.get('tool_input', {}).get('status') != 'complete':
            return {}
    elif name != 'Stop':
        return {}
    root = Path(config['root'])
    with FileLock(root / 'lifecycle.lock', timeout=5):
        path = turn_path(root, event)
        state = read_json(path) if path.exists() else {'pending': {}, 'completed': []}
        call = event.get('tool_use_id', '')
        if name == 'PreToolUse':
            goal, created = current_goal(config, event['session_id'])
            key = event_key(event['session_id'], goal)
            prepared = completion_path(root, key)
            if not prepared.exists():
                return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext':
                    '메인 구현 goal이면 완료 전에 prepare-completion 결과를 확인하세요. '
                    '명세·계획·검증·조사 goal은 보고서 게시 절차 없이 완료합니다.'}}
            record = read_json(prepared)
            if record.get('report_type') != 'implementation_completion' or record.get('report_status') == 'excluded':
                return {}
            if record.get('goal_created_at_ms') and record['goal_created_at_ms'] != created:
                raise ReportError('현재 goal 생성 시각과 준비 기록이 다릅니다.')
            if record['report_status'] == 'verified' and not ReportRuntime(config).verified_receipt(key):
                return {'hookSpecificOutput': {'hookEventName': name, 'permissionDecision': 'deny',
                    'permissionDecisionReason': '보고서 산출물이 변경됐습니다. prepare-completion을 다시 실행하세요.'}}
            state['pending'][call] = {'key': key, 'goal_id': goal, 'created_at_ms': created}
            atomic_json(path, state)
            return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': record['final_response_markdown']}}
        if name == 'PostToolUse':
            pending = state['pending'].get(call)
            if not pending:
                return {}
            for goal in goal_results(event.get('tool_response')):
                if goal.get('threadId', goal.get('thread_id', event['session_id'])) != event['session_id']:
                    continue
                goal_id = goal.get('goalId', goal.get('goal_id'))
                epoch = goal.get('createdAt', goal.get('created_at_ms'))
                if 'createdAt' in goal and isinstance(epoch, (float, int)) and epoch < 100000000000:
                    epoch *= 1000
                matches = goal_id == pending['goal_id'] if goal_id else (
                    isinstance(epoch, (int, float)) and int(epoch) // 1000 == pending['created_at_ms'] // 1000)
                if matches:
                    if pending['key'] not in state['completed']:
                        state['completed'].append(pending['key'])
                    state['pending'].pop(call, None)
                    atomic_json(path, state)
                    break
            return {}
        failures = []
        records = []
        for key in state['completed']:
            try:
                record = read_json(completion_path(root, key))
                if record.get('report_type') == 'implementation_completion' and record.get('report_status') != 'excluded':
                    records.append(record)
            except (OSError, ValueError, ReportError) as error:
                failures.append(str(error))
        urls = {record['url'] for record in records if record.get('report_status') == 'verified'}
        for record in records:
            try:
                check_answer(config, record, event.get('last_assistant_message') or '', urls)
            except (OSError, ValueError, ReportError) as error:
                failures.append(str(error) + '\n' + record.get('final_response_markdown', ''))
        if not records and not failures:
            return {}
        state['answer_check'] = 'failed' if failures else 'passed'
        atomic_json(path, state)
        if not failures:
            return {}
        message = '개발 goal 상태를 변경하거나 재배포·Telegram 전송하지 말고 완료 답변을 보정하세요.\n' + '\n'.join(failures)
        if event.get('stop_hook_active'):
            return {'systemMessage': '보고서 답변 검사 미해결. 자동 보정 반복을 중단합니다.\n' + message}
        return {'decision': 'block', 'reason': message}


def main():
    try:
        result = handle(json.load(sys.stdin), load_config())
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ReportError):
        # A hook failure must not silently claim a report passed or undo a completed development goal.
        result = {'systemMessage': '보고서 hook 검사를 수행하지 못했습니다. 완료 준비 명령의 결과를 직접 확인하세요.'}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
