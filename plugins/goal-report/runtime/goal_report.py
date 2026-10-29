"""보고서 입력·HTML·누적 자산·배포 검증. 알림은 이 모듈의 책임이 아니다."""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import unicodedata
import subprocess
import time
from datetime import datetime, timezone
from contextlib import AbstractContextManager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

MAX_ASSET_BYTES = 24 * 1024 * 1024
MAX_ASSETS = 20_000
STATUSES = {'passed': '통과', 'failed': '실패', 'not-run': '미실행'}
KINDS = {'implementation', 'game', 'research', 'verification'}
REPORT_TYPES = {'implementation_completion', 'specification', 'implementation_plan',
                'verification_report', 'research_report', 'other'}
EDITORIAL_FONT_ASSETS = ('RIDIBatang.otf', 'RIDIBatang-LICENSE.html')
SHARED_FONT_DIRECTORY = 'fonts/ridibatang/v1'
SHARED_FONT_PATHS = tuple(SHARED_FONT_DIRECTORY + '/' + name for name in EDITORIAL_FONT_ASSETS)
PUBLICATION_FIELDS = ('publication_order', 'publication_order_source')


def publication_order(item):
    value = item.get('publication_order')
    return value if type(value) is int and value > 0 else 0


def publication_metadata(item):
    result = {'publication_order': publication_order(item)} if publication_order(item) else {}
    if isinstance(item.get('publication_order_source'), dict):
        result['publication_order_source'] = item['publication_order_source']
    return result


def with_publication_metadata(item, published):
    """입력 manifest의 임의 순번 대신 원장/후보에서 확인한 메타데이터만 병합한다."""
    return {name: value for name, value in item.items() if name not in PUBLICATION_FIELDS} | publication_metadata(published)


def publication_snapshot_digest(reports):
    snapshot = {key: publication_metadata(item) for key, item in reports.items()}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def is_upload_target(report):
    """내용 형식이나 제목으로 문서 목적을 추정하지 않는다."""
    return (report.get('report_type') == 'implementation_completion'
            and report.get('kind') in ('implementation', 'game'))


class ReportError(RuntimeError):
    def __init__(self, message, outcome=None):
        super().__init__(message)
        self.outcome = outcome or {}


class FileLock(AbstractContextManager):
    """An OS-owned lock releases on process death; no stale lock deletion is needed."""
    def __init__(self, path: Path, timeout: float = 300):
        self.path, self.timeout, self.stream = Path(path), timeout, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.stream.write(b'0')
            self.stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise ReportError('다른 보고서 처리 작업이 실행 중입니다. 나중에 재시도하세요.')
                time.sleep(.05)

    def __exit__(self, *_):
        self.stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(value, encoding='utf-8', newline='\n')
    os.replace(temp, path)


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f'.{os.getpid()}.tmp')
    shutil.copyfile(source, temp)
    os.replace(temp, target)


def asset_path(root, relative):
    raw = Path(relative)
    root = Path(root).resolve()
    if raw.is_absolute() or '..' in raw.parts or ':' in relative or '\\' in relative:
        raise ReportError('게시 자산 경로가 잘못되었습니다.')
    target = root / raw
    if not target.resolve().is_relative_to(root):
        raise ReportError('게시 자산이 후보 경로를 벗어났습니다.')
    return target


def published_entries(index):
    """Read historical titles from the approved index, never from a pending input."""
    index = Path(index)
    output_root = index.parents[2] if index.parent.parent.name == 'projects' else index.parent
    class IndexReader(HTMLParser):
        def __init__(self):
            super().__init__()
            self.items, self.item, self.field = {}, None, None
        def handle_starttag(self, tag, attrs):
            if tag == 'li':
                self.item = {'title': [], 'conclusion': []}
            elif self.item is not None and tag == 'a':
                href = dict(attrs).get('href', '')
                try:
                    relative = (index.parent / href).resolve().relative_to(output_root.resolve()).as_posix()
                except (ValueError, OSError):
                    relative = ''
                match = re.fullmatch(r'projects/([^/]+)/([0-9a-f]{24})(?:/index\.html)?', relative)
                if match:
                    self.item.update(project=match[1], key=match[2])
                    self.field = 'title'
            elif self.item is not None and tag == 'p':
                self.field = 'conclusion'
        def handle_data(self, data):
            if self.item is not None and self.field:
                self.item[self.field].append(data)
        def handle_endtag(self, tag):
            if tag in {'a', 'p'}:
                self.field = None
            elif tag == 'li' and self.item is not None:
                if 'key' in self.item:
                    self.items[self.item['key']] = {'project': self.item['project'],
                        'title': ''.join(self.item['title']), 'conclusion': ''.join(self.item['conclusion'])}
                self.item, self.field = None, None
    parser = IndexReader()
    parser.feed(Path(index).read_text(encoding='utf-8'))
    return parser.items


def project_slug(name):
    result = re.sub(r'[^a-z0-9_-]+', '-', unicodedata.normalize('NFKD', name).lower()).strip('-')
    if not result:
        raise ReportError('원본 프로젝트 이름을 경로로 표현할 수 없습니다.')
    return result


def project_identity(report):
    """원본 경로를 확인한 경우에만 URL과 별도로 프로젝트를 분류한다."""
    origin = report.get('project_origin')
    if not origin:
        return {'canonical_project': report['project'], 'display_name': report.get('project_name', report['project'])}
    origin = Path(origin).resolve()
    if not origin.is_dir():
        raise ReportError('project_origin 원본 프로젝트 경로가 없습니다.')
    repository = origin
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        result = subprocess.run(['git', '-C', str(origin), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, text=True, encoding='utf-8', timeout=5, creationflags=flags)
        if result.returncode == 0:
            common = Path(result.stdout.strip()).resolve()
            if common.name == '.git':
                repository = common.parent
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'canonical_project': project_slug(repository.name), 'display_name': repository.name,
            'project_origin': str(repository)}


def catalogue_identity(item):
    canonical = item.get('canonical_project', item['project'])
    if not isinstance(canonical, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', canonical):
        raise ReportError('프로젝트 목록의 canonical 경로가 잘못되었습니다.')
    return canonical, item.get('display_name', canonical)


def navigation_migration(before, href):
    """기존 페이지의 정확한 탐색 요소만 변경하며 나머지 바이트를 보존한다."""
    pattern = rb'(<a (?:class="back" )?href=")[^"<>]*(">\xe2\x86\x90 \xeb\xb3\xb4\xea\xb3\xa0\xec\x84\x9c \xeb\xaa\xa8\xec\x9d\x8c</a>)'
    matches = list(re.finditer(pattern, before))
    if len(matches) != 1:
        return before
    return re.sub(pattern, lambda match: match[1] + href.encode('utf-8') + match[2], before, count=1)


def preview_metadata(page):
    """로컬 입력을 재렌더하지 않고 실제 보존되는 페이지의 제목과 결론을 읽는다."""
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values, self.field = {}, None
        def handle_starttag(self, tag, attrs):
            if tag in {'title', 'h1'}:
                self.field = tag
                self.values.setdefault(tag, [])
            elif tag == 'p' and 'conclusion' in dict(attrs).get('class', '').split():
                self.field = 'conclusion'
                self.values.setdefault('conclusion', [])
        def handle_data(self, data):
            if self.field:
                self.values[self.field].append(data)
        def handle_endtag(self, tag):
            if tag in {'title', 'h1', 'p'}:
                self.field = None
    reader = Reader()
    reader.feed(Path(page).read_text(encoding='utf-8'))
    values = {name: ''.join(parts) for name, parts in reader.values.items()}
    if 'h1' in values:
        values['title'] = values.pop('h1')
    return values


def render_catalogue(output_root, reports, publish=False):
    """root → 프로젝트 → 기존 보고서 주소. 기존 페이지는 탐색 링크만 이전한다."""
    output_root = Path(output_root)
    groups, migrations = {}, []
    entries = dict(reports)
    for path in output_root.glob('projects/*/*/index.html'):
        key = path.parent.name
        entries.setdefault(key, {'project': path.parent.parent.name, 'title': '보관 보고서 · ' + key, 'conclusion': ''})
    for key, item in sorted(entries.items()):
        page = output_root / 'projects' / item['project'] / key / 'index.html'
        if not page.is_file():
            continue
        canonical, display = catalogue_identity(item)
        target = output_root / 'projects' / canonical / 'index.html'
        href = os.path.relpath(target, page.parent).replace('\\', '/')
        before = page.read_bytes()
        after = navigation_migration(before, href)
        if after != before:
            atomic_text(page, after.decode('utf-8'))
            migrations.append({'path': page.relative_to(output_root).as_posix(),
                               'before_sha256': hashlib.sha256(before).hexdigest(),
                               'after_sha256': hashlib.sha256(after).hexdigest(), 'href': href})
        group = groups.setdefault(canonical, {'display_name': display, 'links': []})
        report_href = os.path.relpath(page, target.parent).replace('\\', '/')
        group['links'].append((key, item, f'<li><h2><a href="{esc(report_href)}">{esc(item["title"])}</a></h2><p class="catalogue-conclusion">{esc(item.get("conclusion", ""))}</p></li>'))
    links = []
    for canonical, group in sorted(groups.items()):
        page = output_root / 'projects' / canonical / 'index.html'
        group['links'] = [row[2] for row in sorted(group['links'], key=lambda row: (-publication_order(row[1]), row[0]))]
        atomic_text(page, '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>' + esc(group['display_name']) + ' 보고서</title><link rel="stylesheet" href="../../catalogue-theme.css"></head><body><main class="catalogue"><nav aria-label="보고서 탐색"><a class="back" href="../../index.html">← 전체 프로젝트</a></nav><header><p class="catalogue-kind">프로젝트 보고서</p><h1>' + esc(group['display_name']) + '</h1><p class="catalogue-meta">' + str(len(group['links'])) + '개 보고서</p></header><ul class="report-list">' + ''.join(group['links']) + '</ul></main></body></html>')
        links.append(f'<li><h2><a href="projects/{esc(canonical)}/index.html">{esc(group["display_name"])}</a></h2><p class="catalogue-count">{len(group["links"])}개 보고서</p></li>')
    atomic_text(output_root / 'index.html', '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>Codex 프로젝트 보고서</title><link rel="stylesheet" href="catalogue-theme.css"></head><body><main class="catalogue"><header><p class="catalogue-kind">보고서 모음</p><h1>프로젝트별 보고서</h1></header><ul class="report-list">' + ''.join(links) + '</ul></main></body></html>')
    theme = Path(__file__).with_name('catalogue-theme.css').read_text(encoding='utf-8')
    if not publish:
        theme = theme.replace('src: url("fonts/ridibatang/v1/RIDIBatang.otf") format("opentype");', 'src: local("RIDIBatang"), local("리디바탕");')
    atomic_text(output_root / 'catalogue-theme.css', theme)
    return migrations


def reject_credentials(value):
    # Defense in depth. Producers must still inspect selected text and visual media.
    serialized = json.dumps(value, ensure_ascii=False)
    patterns = (r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b', r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
                r'(?i)Bearer\s+[A-Za-z0-9_.-]{20,}',
                r'(?i)(?:api[_-]?key|access[_-]?token|bot[_-]?token|client[_-]?secret|password)\s*[=:]\s*[\"\x27]?[A-Za-z0-9_./+-]{16,}')
    if any(re.search(pattern, serialized) for pattern in patterns):
        raise ReportError('선택한 보고서/로그에 자격증명 형식이 있습니다. 민감 내용을 제거한 입력을 사용하세요.')


def event_key(thread_id: str, goal_id: str) -> str:
    if not thread_id or not goal_id:
        raise ReportError('thread_id와 goal_id가 필요합니다.')
    return hashlib.sha256((thread_id + '\x1f' + goal_id).encode()).hexdigest()[:24]


def report_key(report):
    """기존 goal 주소를 유지하고 일반 보고서는 저장된 자동 ID로 찾는다."""
    if report.get('goal_id'):
        return event_key(report.get('thread_id'), report['goal_id'])
    key = report.get('report_id')
    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{24}', key):
        raise ReportError('자동 보고서 ID가 없거나 잘못되었습니다.')
    return key


def submitted_goal_identity(report, root):
    """Snapshot the exact producer goal before completion can remove its DB row."""
    supplied = report.get('goal_created_at_ms')
    if supplied is not None and (type(supplied) is not int or supplied <= 0):
        raise ReportError('goal_created_at_ms는 실제 goal의 양수 정수 생성 시각이어야 합니다.')
    epochs = set()
    if supplied is not None:
        epochs.add(supplied)
    for db in (root.parent / 'goals_1.sqlite', root.parent / 'sqlite' / 'goals_1.sqlite'):
        if not db.is_file():
            continue
        conn = None
        try:
            conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
            row = conn.execute('SELECT created_at_ms FROM thread_goals WHERE thread_id=? AND goal_id=?',
                               (report['thread_id'], report['goal_id'])).fetchone()
            if row and type(row[0]) is int and row[0] > 0:
                epochs.add(row[0])
        except sqlite3.Error:
            # An unavailable DB is not permission to pick another thread/goal.
            pass
        finally:
            if conn is not None:
                conn.close()
    if len({epoch // 1000 for epoch in epochs}) > 1:
        raise ReportError('명시한 goal 생성 시각과 저장된 goal ID의 생성 시각이 일치하지 않습니다.')
    if not epochs:
        return None
    return {'thread_id': report['thread_id'], 'goal_id': report['goal_id'],
            'created_at_ms': max(epochs)}


def verified_goal_identity(report, config):
    candidates = [Path(config['goal_database'])] if config.get('goal_database') else [
        Path(config['root']).parent / 'goals_1.sqlite', Path(config['root']).parent / 'sqlite' / 'goals_1.sqlite']
    matches = set()
    for path in candidates:
        if not path.is_file():
            continue
        connection = None
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
            for created, in connection.execute('SELECT created_at_ms FROM thread_goals WHERE thread_id=? AND goal_id=?', (report.get('thread_id'), report.get('goal_id'))):
                if type(created) is int and created > 0:
                    matches.add(created)
        except sqlite3.Error:
            pass
        finally:
            if connection is not None:
                connection.close()
    supplied = report.get('goal_created_at_ms')
    if len(matches) != 1 or (supplied is not None and supplied != next(iter(matches))):
        return None
    return {'thread_id': report['thread_id'], 'goal_id': report['goal_id'],
            'created_at_ms': next(iter(matches)), 'verified_source': 'goal_database'}


def text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f'{label}: 비어 있지 않은 문자열이 필요합니다.')
    return value


def source_path(root: Path, relative: str) -> Path:
    relative = text(relative, 'evidence path')
    raw = Path(relative)
    if raw.is_absolute() or '..' in raw.parts or ':' in relative or '\\' in relative:
        raise ReportError('증거 경로는 evidence_root 내부의 상대 경로여야 합니다.')
    resolved_root, path = root.resolve(), (root / raw).resolve()
    if not path.is_relative_to(resolved_root) or not path.is_file():
        raise ReportError('증거 파일이 없거나 evidence_root를 벗어납니다.')
    forbidden = {'sessions', 'archived_sessions', 'secrets', '.sandbox-secrets', 'mcp-oauth', 'keyring'}
    if any(part.lower() in forbidden for part in path.parts) or path.name.lower().startswith('.env'):
        raise ReportError('자격증명 또는 세션 경로는 증거로 사용할 수 없습니다.')
    return path


def validate_manifest(raw, root: Path):
    if not isinstance(raw, dict) or raw.get('schema_version') != 1:
        raise ReportError('schema_version=1인 보고서 객체가 필요합니다.')
    value = dict(raw)
    for key in ('project', 'title', 'conclusion'):
        text(value.get(key), key)
    for key in ('thread_id', 'goal_id'):
        if key in value:
            text(value[key], key)
            if value[key].upper().startswith('REPLACE'):
                raise ReportError('예제 ID를 실제 thread_id와 goal_id로 교체하세요.')
    if value.get('goal_id'):
        text(value.get('thread_id'), 'thread_id')
    elif 'goal_created_at_ms' in value:
        raise ReportError('goal 없는 보고서에는 goal 생성 시각을 지정하지 않습니다.')
    if 'report_id' in value:
        raise ReportError('보고서 ID는 자동 생성합니다. 입력에서 report_id를 제거하세요.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', value['project']):
        raise ReportError('project는 소문자·숫자·밑줄·하이픈으로 지정하세요.')
    if value.get('kind') not in KINDS:
        raise ReportError('지원하지 않는 보고서 유형입니다.')
    value.setdefault('report_type', 'other')
    if not isinstance(value['report_type'], str) or value['report_type'] not in REPORT_TYPES:
        raise ReportError('지원하지 않는 report_type입니다.')
    if value['report_type'] == 'implementation_completion' and not is_upload_target(value):
        raise ReportError('구현 완료 보고서의 kind는 implementation 또는 game이어야 합니다.')
    for key in ('changes', 'tests', 'caveats', 'sections', 'media', 'tables', 'charts'):
        if not isinstance(value.get(key, []), list):
            raise ReportError(f'{key}는 배열이어야 합니다.')
        value.setdefault(key, [])
    for key in ('tests', 'sections', 'media', 'tables', 'charts'):
        if any(not isinstance(item, dict) for item in value[key]):
            raise ReportError(f'{key} 항목은 객체여야 합니다.')
    if not value['tests']:
        raise ReportError('검증 결과 또는 미실행 사유를 tests에 명시하세요.')
    for test in value['tests']:
        text(test.get('name'), 'test name')
        if test.get('status') not in STATUSES:
            raise ReportError('검증 상태는 passed, failed, not-run 중 하나여야 합니다.')
        text(test.get('details'), 'test details')
    for item in value['changes'] + value['caveats']:
        text(item, 'description')
    for section in value['sections']:
        text(section.get('title'), 'section title')
        text(section.get('text'), 'section text')
    for table in value['tables']:
        text(table.get('title'), 'table title')
        text(table.get('conditions'), 'table conditions')
        text(table.get('source'), 'table source')
        cols = table.get('columns')
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
            raise ReportError('비교표 열이 필요합니다.')
        if not isinstance(table.get('rows'), list) or not table['rows']:
            raise ReportError('비교표 데이터가 필요합니다.')
        for row in table['rows']:
            if not isinstance(row, list) or len(row) != len(cols) or any(isinstance(c, (dict, list)) for c in row):
                raise ReportError('비교표의 열 수가 맞지 않습니다.')
    for chart in value['charts']:
        for key in ('title', 'unit', 'conditions', 'source'):
            text(chart.get(key), 'chart ' + key)
        if not isinstance(chart.get('values'), list) or not chart['values']:
            raise ReportError('차트 수치가 필요합니다.')
        for point in chart['values']:
            if not isinstance(point, dict):
                raise ReportError('차트 데이터 항목은 객체여야 합니다.')
            text(point.get('label'), 'chart label')
            n = point.get('value')
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
                raise ReportError('차트 값은 유한한 수치여야 합니다.')
    if value['kind'] in {'research', 'verification'} and not value['tables']:
        raise ReportError('검증·조사 보고서에는 조건과 출처가 있는 비교표가 필요합니다.')
    # Only explicitly selected media/log files are copied. There is no directory scan.
    for media in value['media']:
        text(media.get('caption'), 'media caption')
        if media.get('role') not in {'execution', 'illustration'}:
            raise ReportError('미디어 역할은 execution 또는 illustration입니다.')
        path = source_path(root, media.get('path'))
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.mp4'}:
            raise ReportError('지원하는 이미지 또는 MP4만 선택하세요.')
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise ReportError('미디어가 24 MiB를 초과합니다. 원본을 유지하고 장면별로 처리하세요.')
        if path.suffix.lower() == '.mp4':
            from report_media import inspect_media
            info = inspect_media(path)
            stream = info['video']
            w, h = stream['width'], stream['height']
            cap = (1920, 1080) if w >= h else (1080, 1920)
            if w > cap[0] or h > cap[1]:
                raise ReportError('영상 해상도가 방향별 최대 해상도를 초과합니다.')
            if stream.get('codec_name') not in {'h264', 'av1'}:
                raise ReportError('지원하는 H.264 또는 AV1 영상이 필요합니다.')
            media['metadata'] = info
        else:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                w, h = image.size
            cap = (1920, 1080) if w >= h else (1080, 1920)
            if w > cap[0] or h > cap[1]:
                raise ReportError('이미지가 방향별 최대 해상도를 초과합니다.')
            media['metadata'] = {'width': w, 'height': h}
        if media.get('poster'):
            poster = source_path(root, media['poster'])
            if poster.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'} or poster.stat().st_size > MAX_ASSET_BYTES:
                raise ReportError('영상 포스터는 24 MiB 이하 이미지여야 합니다.')
            from PIL import Image
            with Image.open(poster) as image:
                image.verify()
            with Image.open(poster) as image:
                w, h = image.size
            cap = (1920, 1080) if w >= h else (1080, 1920)
            if w > cap[0] or h > cap[1]:
                raise ReportError('포스터가 방향별 최대 해상도를 초과합니다.')
    for section in value['sections']:
        if section.get('log_path'):
            log = source_path(root, section['log_path'])
            if log.suffix.lower() not in {'.txt', '.log', '.md'} or log.stat().st_size > MAX_ASSET_BYTES:
                raise ReportError('기술 로그는 명시한 텍스트 파일이어야 합니다.')
    value.pop('evidence_missing', None)
    if value['kind'] == 'game' and not any(m['role'] == 'execution' for m in value['media']):
        value['evidence_missing'] = True
        value['caveats'].append('필수 증거 누락: 실제 게임 실행 이미지 또는 영상이 없습니다.')
    return value


def esc(value):
    return html.escape(str(value), quote=True)


def paragraph(value):
    return '<p>' + esc(value).replace('\n', '<br>') + '</p>'


def render_html(report):
    checks = ''.join(f'<div class="check {esc(t["status"])}"><dt>{STATUSES[t["status"]]} · {esc(t["name"])}</dt><dd>{paragraph(t["details"])}</dd></div>' for t in report['tests'])
    changes = ''.join('<li>' + esc(v) + '</li>' for v in report['changes'])
    caveats = ''.join('<li>' + esc(v) + '</li>' for v in report['caveats'])
    sections = []
    for s in report['sections']:
        sections.append('<section class="ecr-section"><h2>' + esc(s['title']) + '</h2>' + paragraph(s['text']) + '</section>')
    for m in report['media']:
        path = esc(m['path'])
        if m['path'].endswith('.mp4'):
            poster = f' poster="{esc(m["poster"])}"' if m.get('poster') else ''
            element = f'<video controls playsinline preload="metadata"{poster}><source src="{path}" type="video/mp4">영상 재생이 지원되지 않으면 원본 링크를 이용하세요.</video>'
        else:
            element = f'<img src="{path}" alt="{esc(m["caption"])}" loading="lazy">'
        sections.append(f'<section class="ecr-section media"><h2>{"실행 증거" if m["role"] == "execution" else "참고 이미지·영상"}</h2><figure>{element}<figcaption>{esc(m["caption"])}</figcaption></figure><a href="{path}">미디어 원본 열기</a></section>')
    for table in report['tables']:
        head = ''.join('<th scope="col">' + esc(c) + '</th>' for c in table['columns'])
        rows = ''.join('<tr>' + ''.join('<td>' + esc(c) + '</td>' for c in row) + '</tr>' for row in table['rows'])
        sections.append(f'<section class="ecr-section"><h2>{esc(table["title"])}</h2><div class="table-scroll" tabindex="0" role="region" aria-label="{esc(table["title"])}"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div><p class="caption">조건: {esc(table["conditions"])}<br>출처: {esc(table["source"])}</p></section>')
    for chart in report['charts']:
        max_value = max(abs(p['value']) for p in chart['values']) or 1
        bars = ''.join(f'<li><span>{esc(p["label"])}</span><div class="bar-track"><span class="bar" style="width:{abs(p["value"])/max_value*100:.3f}%"></span></div><strong>{esc(p["value"])} {esc(chart["unit"])}</strong></li>' for p in chart['values'])
        sections.append(f'<section class="ecr-section"><h2>{esc(chart["title"])}</h2><ul class="chart">{bars}</ul><p class="caption">조건: {esc(chart["conditions"])}<br>출처: {esc(chart["source"])}</p></section>')
    missing = '<p class="warning">필수 실행 증거 누락 · 보고서 증거 준비 미완료</p>' if report.get('evidence_missing') else ''
    project = esc(report.get('display_name', report['project']))
    kind = '작업 완료 보고' if report['kind'] in {'implementation', 'game'} else '작업 보고'
    conclusion = esc(report['conclusion']).replace('\n', '<br>')
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="color-scheme" content="light"><title>{esc(report['title'])}</title><link rel="stylesheet" href="report.css"></head><body><main id="editorial-completion-report"><nav class="ecr-navigation" aria-label="보고서 탐색"><a class="back" href="../../../index.html">← 보고서 모음</a></nav><div class="ecr-layout"><header class="ecr-header"><p class="ecr-kind">{kind}</p><h1>{esc(report['title'])}</h1><div class="ecr-meta"><span>프로젝트 · {project}</span></div></header><div class="ecr-reading"><p class="conclusion ecr-lead">{conclusion}</p>{missing}<section class="ecr-section"><h2>바뀐 점</h2><ul class="ecr-changes">{changes}</ul></section><section class="ecr-section"><h2>확인 결과</h2><dl class="ecr-checks">{checks}</dl></section>{''.join(sections)}</div><aside class="ecr-margin" aria-labelledby="ecr-remaining-title"><h2 id="ecr-remaining-title">주의점과 미확인 사항</h2><ul>{caveats or '<li>추가로 보고된 항목 없음</li>'}</ul></aside></div></main></body></html>'''


class ReportStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.public = self.root / 'public'

    def manifest_path(self, key):
        if not re.fullmatch('[0-9a-f]{24}', key):
            raise ReportError('잘못된 report key입니다.')
        return self.root / 'inputs' / key / 'manifest.json'

    def submit(self, source: Path):
        source = Path(source).resolve()
        raw = read_json(source)
        evidence_root = Path(raw.get('evidence_root', source.parent))
        if not evidence_root.is_absolute():
            evidence_root = source.parent / evidence_root
        report = validate_manifest(raw, evidence_root)
        report.update(project_identity(report))
        identity = submitted_goal_identity(report, self.root) if report.get('goal_id') else None
        with FileLock(self.root / 'store.lock'):
            binding = None
            if report.get('goal_id'):
                key = event_key(report['thread_id'], report['goal_id'])
            else:
                source_hash = hashlib.sha256(os.path.normcase(str(source)).encode()).hexdigest()
                binding = self.root / 'report-sources' / (source_hash + '.json')
                if binding.exists():
                    previous = read_json(binding)
                    key = report_key(previous)
                    # Validate before using a binding as a file path.
                    target_manifest = self.manifest_path(key)
                    if previous.get('project') != report['project']:
                        raise ReportError('같은 입력 파일의 프로젝트는 변경하지 않습니다. 새 보고서는 다른 입력 파일로 만드세요.')
                    if target_manifest.exists():
                        saved = read_json(target_manifest)
                        if saved.get('goal_id') or saved.get('report_id') != key:
                            raise ReportError('자동 보고서 ID의 연결이 다른 보고서를 가리킵니다.')
                else:
                    key = secrets.token_hex(12)
                    while self.manifest_path(key).exists():
                        key = secrets.token_hex(12)
                report['report_id'] = key
            job_path = self.root / 'jobs' / f'{key}.json'
            if job_path.exists():
                job = read_json(job_path)
                if job.get('completion') in {'sending', 'uncertain', 'sent'} or job.get('recovery_notice'):
                    raise ReportError('이미 전달된 보고서 입력은 변경하지 않습니다. 새 goal로 후속 보고서를 만드세요.')
            target = self.manifest_path(key).parent
            target.mkdir(parents=True, exist_ok=True)
            identity_path = target / 'identity.json'
            if identity_path.exists():
                previous = read_json(identity_path)
                if identity and any(previous.get(field) != identity.get(field) for field in ('thread_id', 'goal_id', 'created_at_ms')):
                    raise ReportError('이미 기록된 goal ID·생성 시각 연결은 변경하지 않습니다.')
            for i, media in enumerate(report['media']):
                for field in ('path', 'poster'):
                    if media.get(field):
                        original = source_path(evidence_root, media[field])
                        data = original.read_bytes()
                        filename = f'{i}-{field}-{hashlib.sha256(data).hexdigest()[:16]}{original.suffix.lower()}'
                        (target / filename).write_bytes(data)
                        media[field] = filename
                        if field == 'path':
                            media['sha256'] = hashlib.sha256(data).hexdigest()
            for section in report['sections']:
                if section.get('log_path'):
                    section['log_text'] = source_path(evidence_root, section.pop('log_path')).read_text(encoding='utf-8')
            report.pop('evidence_root', None)
            reject_credentials(report)
            atomic_json(self.manifest_path(key), report)
            if identity and not identity_path.exists():
                atomic_json(identity_path, identity)
            if binding:
                atomic_json(binding, {'report_id': key, 'project': report['project']})
        return key

    def remove_managed_local_font_bundle(self, directory):
        """이 보고서의 알려진 이전 생성물 두 개만 확인한 뒤 제거한다."""
        fonts = directory / 'fonts'
        if fonts.is_symlink() or fonts.resolve() != directory.resolve() / 'fonts' or (fonts.exists() and not fonts.is_dir()):
            raise ReportError('로컬 폰트 경로가 일반 보고서 폴더가 아니므로 변경하지 않습니다.')
        managed = []
        for name in EDITORIAL_FONT_ASSETS:
            target = fonts / name
            if target.is_symlink():
                raise ReportError('로컬 폰트 파일이 링크이므로 변경하지 않습니다.')
            if target.exists():
                source = Path(__file__).with_name('fonts') / name
                if not target.is_file() or target.read_bytes() != source.read_bytes():
                    raise ReportError('기존 로컬 폰트 파일이 알려진 생성물과 달라 보존했습니다.')
                managed.append(target)
        for target in managed:
            target.unlink()

    def catalogue_entries(self, published=None):
        if published is None:
            ledger = self.root / 'published.json'
            published = read_json(ledger).get('reports', {}) if ledger.is_file() else {}
        reports = dict(published)
        for path in sorted((self.root / 'inputs').glob('*/manifest.json')):
            reports[path.parent.name] = with_publication_metadata(read_json(path), published.get(path.parent.name, {}))
        return reports

    def render(self, key, publish=False, output_root=None, entries=None):
        report = read_json(self.manifest_path(key))
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', report['project']):
            raise ReportError('저장된 project 경로가 잘못되었습니다.')
        output_root = Path(output_root) if output_root else self.public if publish else self.root / 'previews'
        directory = output_root / 'projects' / report['project'] / key
        if not publish and directory.resolve() != output_root.resolve() / 'projects' / report['project'] / key:
            raise ReportError('로컬 보고서 경로가 원래 출력 위치와 달라 변경하지 않습니다.')
        directory.mkdir(parents=True, exist_ok=True)
        if not publish:
            self.remove_managed_local_font_bundle(directory)
        for media in report['media']:
            for field in ('path', 'poster'):
                if media.get(field):
                    filename = media[field]
                    if Path(filename).name != filename or ':' in filename or '\\' in filename:
                        raise ReportError('저장된 미디어 경로가 잘못되었습니다.')
                    (directory / 'media').mkdir(exist_ok=True)
                    atomic_copy(source_path(self.manifest_path(key).parent, filename), directory / 'media' / filename)
                    media[field] = 'media/' + filename
        atomic_text(directory / 'index.html', render_html(report))
        theme = Path(__file__).with_name('editorial-theme.css').read_text(encoding='utf-8')
        if publish:
            theme = theme.replace('url("fonts/RIDIBatang.otf")', 'url("../../../' + SHARED_FONT_DIRECTORY + '/RIDIBatang.otf")')
            for name in EDITORIAL_FONT_ASSETS:
                source = Path(__file__).with_name('fonts') / name
                target = asset_path(output_root, SHARED_FONT_DIRECTORY + '/' + name)
                if target.exists():
                    if target.is_symlink() or target.read_bytes() != source.read_bytes():
                        raise ReportError('공용 폰트 v1 바이트가 다릅니다. 기존 버전을 덮어쓰지 않고 새 버전이 필요합니다.')
                else:
                    atomic_copy(source, target)
        else:
            theme = theme.replace('src: url("fonts/RIDIBatang.otf") format("opentype");', 'src: local("RIDIBatang"), local("리디바탕");')
        atomic_text(directory / 'report.css', theme)
        if not (output_root / 'theme.css').exists():
            atomic_copy(Path(__file__).with_name('report-theme.css'), output_root / 'theme.css')
        reports = entries if entries is not None else self.catalogue_entries()
        if publish:
            atomic_text(output_root / '_headers', "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Robots-Tag: noindex, nofollow\n  Cache-Control: private, no-store\n  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'\n/fonts/ridibatang/v1/*\n  ! Cache-Control\n  Cache-Control: private, max-age=31536000, immutable\n")
        render_catalogue(output_root, reports, publish=publish)
        return directory / 'index.html'

    def preflight(self, public=None):
        public = Path(public) if public else self.public
        assets = [p for p in public.rglob('*') if p.is_file()]
        if len(assets) > MAX_ASSETS or any(p.stat().st_size > MAX_ASSET_BYTES for p in assets):
            raise ReportError('누적 자산 파일 수 또는 파일 크기가 배포 한도를 초과합니다. 자동 삭제하지 않습니다.')
        allowed = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.webp', '.mp4', '.otf'}
        for path in assets:
            if path.is_symlink() or not path.resolve().is_relative_to(public.resolve()):
                raise ReportError('배포 자산이 보관 경로를 벗어납니다.')
            if path.name != '_headers' and path.suffix.lower() not in allowed:
                raise ReportError('배포 자산에 허용되지 않은 파일이 있습니다.')
        return assets


class ReportRuntime:
    def __init__(self, config, deployer=None, verifier=None):
        self.config = config
        self.store = ReportStore(Path(config['root']))
        self.deployer = deployer or self.deploy
        self.verifier = verifier or (self.inspect if deployer is None and config.get('worker_id') else None)
        self.candidate = None
        self.known_outcome = {}
        self.requested_publication = None
        self.requested_record = None

    def publication_target(self):
        return {name: self.config.get(name) for name in ('base_url', 'account', 'worker_id', 'worker_name',
                'access_host', 'wrangler_version', 'wrangler', 'compatibility_date')}

    def publication_readiness(self, key):
        """명시 CLI의 현재 호출 문맥만 인정하며 저장된 승인 기록을 자동 검색하지 않는다."""
        authorization = self.requested_publication
        if authorization is None:
            return self.completion_readiness(key)
        try:
            report = read_json(self.store.manifest_path(key))
            return (authorization['key'] == key and is_upload_target(report)
                    and authorization['report_context'] == self.report_context(key)
                    and authorization['report_type'] == report['report_type'] and authorization['kind'] == report['kind']
                    and authorization['target'] == self.publication_target()
                    and authorization['input_fingerprint'] == self.input_fingerprint(key)
                    and Path(authorization['request']['path']).read_bytes().decode('utf-8') == authorization['request']['text']
                    and all(hashlib.sha256(Path(authorization[field]['path']).read_bytes()).hexdigest() == authorization[field]['sha256']
                            for field in ('request', 'manifest')))
        except (OSError, ValueError, KeyError, TypeError, ReportError):
            return False

    def publish_requested(self, manifest, request):
        """사용자 원문을 이번 입력·대상에 연결한 호출에서만 기존 게시 파이프라인을 사용한다."""
        if self.requested_publication is not None:
            raise ReportError('명시 게시 호출을 중첩하지 않습니다.')
        manifest, request = Path(manifest).resolve(), Path(request).resolve()
        request_bytes = request.read_bytes()
        request_text = request_bytes.decode('utf-8')
        if not request_text.strip() or len(request_bytes) > 65536:
            raise ReportError('명시 사용자 지시는 비어 있지 않은 64 KiB 이하 UTF-8 파일이어야 합니다.')
        source_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        key = self.store.submit(manifest)
        report = read_json(self.store.manifest_path(key))
        if not is_upload_target(report):
            raise ReportError('명시 게시도 구현 완료 보고서만 허용합니다.')
        self.requested_publication = {'key': key, 'report_context': self.report_context(key),
            'report_type': report['report_type'], 'kind': report['kind'], 'target': self.publication_target(),
            'input_fingerprint': self.input_fingerprint(key),
            'manifest': {'path': str(manifest), 'sha256': source_digest},
            'request': {'path': str(request), 'sha256': hashlib.sha256(request_bytes).hexdigest(), 'text': request_text}}
        try:
            result = self.retry(key)
            if result.get('report_status') == 'excluded':
                raise ReportError('명시 게시 요청의 원문·입력·배포 대상이 변경되어 다시 준비해야 합니다.')
            return dict(result, publication_mode='explicit_user_request')
        finally:
            self.requested_publication = None
            self.requested_record = None

    def requested_candidate_record(self, candidate):
        key = candidate['key']
        if self.requested_publication is None or not self.publication_readiness(key):
            raise ReportError('현재 명시 사용자 지시와 입력·배포 대상이 일치하지 않습니다.')
        dependencies = [asset for asset in candidate['assets'] if asset['path'] in SHARED_FONT_PATHS]
        if {asset['path'] for asset in dependencies} != set(SHARED_FONT_PATHS):
            raise ReportError('공용 폰트 의존 자산이 게시 후보에 없습니다.')
        return {'schema_version': 1, 'purpose': 'explicit_user_publication',
            'authorization': self.requested_publication, 'shared_dependencies': dependencies,
            'candidate': {'public': str(Path(candidate['public']).resolve()), 'candidate_id': candidate['candidate_id'],
                'asset_digest': candidate['asset_digest'], 'baseline_digest': candidate['baseline']['asset_digest'],
                'publication_order_digest': candidate['publication_order_digest'],
                'artifact_fingerprint': self.artifact_fingerprint(key, candidate['public'])}}

    def bind_requested_candidate(self, candidate):
        if self.requested_publication is None:
            return
        record = self.requested_candidate_record(candidate)
        identity = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]
        path = self.store.root / 'publication-requests' / (identity + '.json')
        if path.exists():
            if read_json(path) != record:
                raise ReportError('기존 명시 게시 승인 기록이 다릅니다.')
        else:
            atomic_json(path, record)
        self.requested_record = {'publication_request': str(path.resolve()),
            'publication_request_digest': hashlib.sha256(path.read_bytes()).hexdigest()}

    def activate_requested_publication(self, key, path, digest, candidate):
        """인증 전 별도 policy 프로세스가 동일 비공개 승인·후보 바이트를 재검증한다."""
        path = Path(path)
        if (path.is_symlink() or path.resolve().parent != (self.store.root / 'publication-requests').resolve()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ReportError('명시 게시 승인 기록 경로 또는 해시가 다릅니다.')
        record = read_json(path)
        self.requested_publication = record.get('authorization')
        if (not self.requested_publication or self.requested_publication.get('key') != key
                or record != self.requested_candidate_record(candidate)):
            raise ReportError('명시 게시 원문·입력·대상·후보 연결이 다릅니다.')
        self.requested_record = {'publication_request': str(path.resolve()), 'publication_request_digest': digest}

    def render_local(self, key):
        ledger = self.store.root / 'published.json'
        published = self.published_baseline(persist=False)['reports'] if ledger.is_file() else {}
        return self.store.render(key, entries=self.store.catalogue_entries(published))

    def rebuild_local_catalogue(self):
        """확정된 게시 바이트로 로컬 탐색을 생성하며 원격 기준을 변경하지 않는다."""
        ledger = self.store.root / 'published.json'
        if not ledger.is_file():
            raise ReportError('확정 게시 기준이 없어 기존 목록을 재구성하지 않습니다.')
        baseline = self.published_baseline(persist=False)
        previews = self.store.root / 'previews'
        copied = []
        for asset in baseline['assets']:
            source = self.store.root / 'published-objects' / asset['sha256']
            if not source.is_file():
                source = asset_path(self.store.public, asset['path'])
            if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != asset['sha256']:
                raise ReportError('확정 게시 원본이 손상돼 로컬 목록을 재구성하지 않습니다.')
            target = asset_path(previews, asset['path'])
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ReportError('기존 로컬 미리보기 자산 경로가 일반 파일이 아닙니다.')
            if not target.exists():
                atomic_copy(source, target)
                copied.append(asset['path'])
        entries = dict(baseline.get('reports', {}))
        inputs = {path.parent.name: read_json(path) for path in (self.store.root / 'inputs').glob('*/manifest.json')}
        for page in previews.glob('projects/*/*/index.html'):
            key, project = page.parent.name, page.parent.parent.name
            actual = preview_metadata(page)
            fallback = entries.get(key, {'project': project, 'title': '보관 보고서 · ' + key, 'conclusion': ''})
            source = inputs.get(key)
            # 제출만 바뀐 입력은 유지되는 preview의 목록 제목·분류를 바꾸지 않는다.
            if source and source.get('project') == project and all(actual.get(field) == source.get(field) for field in ('title', 'conclusion')):
                fallback = with_publication_metadata(source, fallback)
            entries[key] = dict(fallback, project=project, **actual)
        migrations = render_catalogue(previews, entries)
        result = {'local_generated': True, 'deployed': False, 'html': (previews / 'index.html').resolve().as_posix(),
                  'copied_assets': len(copied), 'navigation_migrations': migrations,
                  'published_baseline_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}
        atomic_json(self.store.root / 'local-catalogue.json', result)
        return result

    def completion_readiness(self, key):
        report = read_json(self.store.manifest_path(key))
        if not is_upload_target(report) or not report.get('goal_id'):
            return False
        try:
            identity = read_json(self.store.manifest_path(key).with_name('identity.json'))
            ready = read_json(self.store.manifest_path(key).with_name('readiness.json'))
            return (ready.get('schema_version') == 1 and ready.get('completion_ready') is True
                    and ready.get('identity') == identity and identity.get('verified_source') == 'goal_database'
                    and identity.get('thread_id') == report.get('thread_id')
                    and identity.get('goal_id') == report.get('goal_id')
                    and ready.get('input_fingerprint') == self.input_fingerprint(key))
        except (OSError, ValueError, ReportError):
            return False

    def seal_completion(self, key):
        report = read_json(self.store.manifest_path(key))
        if not is_upload_target(report):
            return
        if report.get('completion_ready') is not True:
            return
        identity = verified_goal_identity(report, self.config)
        if not identity:
            raise ReportError('실제 goal DB에서 정확한 thread·goal·생성 시각을 확인하지 못했습니다.')
        atomic_json(self.store.manifest_path(key).with_name('identity.json'), identity)
        atomic_json(self.store.manifest_path(key).with_name('readiness.json'), {
            'schema_version': 1, 'completion_ready': True, 'identity': identity,
            'input_fingerprint': self.input_fingerprint(key), 'prepared_at_ms': int(time.time() * 1000)})

    def deployment_request(self, public, report_path, key):
        report = read_json(self.store.manifest_path(key))
        if not self.publication_readiness(key) or not is_upload_target(self.candidate or {}):
            raise ReportError('Cloudflare 업로드는 구현 완료 보고서만 허용합니다.')
        baseline = self.candidate['baseline']
        request = dict(self.config, public=str(Path(public).resolve()), report_path=report_path,
            report_type=self.candidate['report_type'], kind=self.candidate['kind'],
            navigation_migrations=self.candidate.get('navigation_migrations', []),
            thread_id=report.get('thread_id'), goal_id=report.get('goal_id'), event_key=key, previous_assets=baseline['assets'], baseline_digest=baseline.get('asset_digest'),
            candidate_digest=self.candidate['asset_digest'], changed_paths=self.candidate['changed_paths'], log_progress=True)
        request['publication_order_digest'] = self.candidate['publication_order_digest']
        if self.requested_publication is not None:
            if not self.requested_record:
                raise ReportError('후보 바이트에 연결된 명시 게시 승인 기록이 없습니다.')
            self.activate_requested_publication(key, self.requested_record['publication_request'],
                self.requested_record['publication_request_digest'], self.candidate)
            request.update(self.requested_record)
        return request

    def deploy(self, public: Path, report_path: str, key: str):
        return self.node_request(self.deployment_request(public, report_path, key))

    def node_request(self, request):
        import sys
        import threading
        timeout = request.get('deployment_timeout_seconds', 300) + 15
        output = {}
        process = subprocess.Popen([self.config.get('node', 'node'), str(Path(__file__).with_name('report-deploy.cjs'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        def consume():
            for line in process.stdout:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and ('verified' in value or 'assets' in value):
                        output.update(value)
                    elif isinstance(value, dict) and 'phase' in value:
                        print(json.dumps(value, ensure_ascii=False), file=sys.stderr, flush=True)
                except ValueError:
                    pass
        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        try:
            process.stdin.write(json.dumps(request))
            process.stdin.close()
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            journal = Path(request.get('root', self.store.root)) / 'deployment-attempts' / (str(request.get('event_key', '')) + '.json')
            outcome = read_json(journal) if journal.is_file() else {}
            if not request.get('candidate_digest') or outcome.get('candidate_digest') != request['candidate_digest']:
                outcome = {'deployed': None, 'deployment_status': 'unknown'}
            raise ReportError('배포·검증 시간 초과. 마지막 원격 적용 상태를 기록했습니다.', outcome) from None
        finally:
            reader.join(5)
            process.stdout.close()
        if not output:
            raise ReportError('배포 결과를 확인하지 못했습니다. 새 링크를 보내지 않았습니다.', {'deployed': None, 'deployment_status': 'unknown'})
        if process.returncode:
            raise ReportError(output.get('error', '배포 또는 보호 확인 실패'), output)
        return output

    def describe_assets(self, public):
        return self.node_request({'action': 'manifest', 'public': str(Path(public).resolve())})

    def inspect(self, public, route, key, expected_digest):
        if not self.publication_readiness(key):
            raise ReportError('goal 완료 준비가 유효하지 않아 원격 조회하지 않습니다.')
        request = self.deployment_request(public, route, key)
        request.update(action='status', expected_asset_digest=expected_digest, candidate_digest=expected_digest, changed_paths=[])
        return self.node_request(request)

    def published_baseline(self, *, persist=True):
        path = self.store.root / 'published.json'
        if path.exists():
            baseline = read_json(path)
            if baseline.get('publication_order_schema') != 1:
                baseline = self.migrate_publication_order(baseline)
                if persist:
                    atomic_json(path, baseline)
            return baseline
        baseline = {'schema_version': 1, 'assets': [], 'reports': {}, 'asset_digest': None, 'version_id': None}
        if self.store.public.exists() and any(self.store.public.iterdir()):
            description = self.describe_assets(self.store.public)
            receipts = [read_json(p) for p in (self.store.root / 'verification').glob('*.json')]
            receipts = sorted((r for r in receipts if r.get('verified') and r.get('asset_digest')), key=lambda r: r.get('checked_at', ''), reverse=True)
            if receipts and receipts[0]['asset_digest'] != description['asset_digest']:
                raise ReportError('기존 게시 폴더가 마지막 확인된 배포와 다릅니다. 자동으로 게시 기준에 합류시키지 않습니다.')
            baseline.update(description, version_id=receipts[0].get('version_id') if receipts else None)
            historical = published_entries(self.store.public / 'index.html')
            for index in self.store.public.glob('projects/*/index.html'):
                # Ledger normally carries metadata; legacy titles remain available in per-project indexes.
                for key, value in published_entries(index).items():
                    historical.setdefault(key, value)
            for asset in baseline['assets']:
                self.keep_object(asset_path(self.store.public, asset['path']), asset)
                match = re.fullmatch(r'projects/([^/]+)/([0-9a-f]{24})/index\.html', asset['path'])
                if match:
                    project, key = match.groups()
                    baseline['reports'][key] = historical.get(key, {'project': project,
                        'title': '보관 보고서 · ' + key, 'conclusion': ''})
        baseline = self.migrate_publication_order(baseline)
        if persist:
            atomic_json(path, baseline)
        return baseline

    def migrate_publication_order(self, baseline):
        """검증된 과거 receipt 시각을 최초 한 번만 순서 추정에 사용한다."""
        reports, evidence = {}, []
        for key, item in baseline.get('reports', {}).items():
            reports[key] = with_publication_metadata(item, {'publication_order_source': {'kind': 'unknown_legacy'}})
            if not re.fullmatch('[0-9a-f]{24}', key):
                continue
            path = self.store.root / 'verification' / (key + '.json')
            try:
                receipt = read_json(path)
                expected = self.config['base_url'].rstrip('/') + f'/projects/{item["project"]}/{key}/'
                if (receipt.get('verified') is not True or receipt.get('deployed', True) is not True
                        or receipt.get('deployment_status') in {'failed', 'unknown', 'applied_unverified', 'excluded', 'not_applied', 'stale'}
                        or receipt.get('key', key) != key or receipt.get('project', item['project']) != item['project']
                        or receipt.get('url') != expected):
                    continue
                version, digest = receipt.get('version_id'), receipt.get('asset_digest')
                if (not isinstance(version, str) or not version.strip()
                        or not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest)):
                    continue
                stamp = datetime.fromisoformat(receipt['checked_at'].replace('Z', '+00:00'))
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    continue
                stamp = stamp.astimezone(timezone.utc)
                evidence.append((stamp, key, path.relative_to(self.store.root).as_posix()))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                continue
        order, previous = 0, None
        for stamp, key, receipt_path in sorted(evidence):
            if stamp != previous:
                order += 1
            previous = stamp
            reports[key].update(publication_order=order, publication_order_source={
                'kind': 'verified_receipt_estimate', 'checked_at': stamp.isoformat(), 'receipt': receipt_path})
        return dict(baseline, reports=reports, publication_order_schema=1,
                    publication_order_migration={'kind': 'verified_receipt_estimate',
                        'known_reports': len(evidence), 'unknown_reports': len(reports) - len(evidence)})

    def keep_object(self, source, asset):
        digest = asset['sha256']
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise ReportError('게시 자산 해시가 잘못되었습니다.')
        target = self.store.root / 'published-objects' / digest
        if not target.exists():
            if hashlib.sha256(Path(source).read_bytes()).hexdigest() != digest:
                raise ReportError('게시 후보 바이트가 기록과 다릅니다.')
            atomic_copy(source, target)
        elif target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ReportError('확정 자산 보관 파일이 손상됐습니다. 자동으로 덮어쓰지 않습니다.')
        return target

    def input_fingerprint(self, key):
        report = read_json(self.store.manifest_path(key))
        paths = [self.store.manifest_path(key), Path(__file__).with_name('report-policy.py'), Path(__file__), Path(__file__).with_name('report-deploy.cjs'), Path(__file__).with_name('report-theme.css'), Path(__file__).with_name('editorial-theme.css'), Path(__file__).with_name('catalogue-theme.css')]
        paths += [Path(__file__).with_name('fonts') / name for name in EDITORIAL_FONT_ASSETS]
        for media in report['media']:
            paths += [source_path(self.store.manifest_path(key).parent, media[field]) for field in ('path', 'poster') if media.get(field)]
        target = {name: self.config.get(name) for name in ('base_url', 'account', 'worker_id', 'worker_name', 'access_host', 'wrangler_version')}
        return hashlib.sha256(json.dumps([target, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]], sort_keys=True).encode()).hexdigest()

    def build_candidate(self, key, baseline):
        fingerprint = self.input_fingerprint(key)
        ordering_digest = publication_snapshot_digest(baseline['reports'])
        candidate_id = hashlib.sha256((fingerprint + str(baseline['asset_digest']) + ordering_digest).encode()).hexdigest()
        root = self.store.root / 'candidates' / key / candidate_id
        public = root / 'public'
        # Recreate a candidate only from the confirmed ledger; failed candidates are never sources.
        if not public.exists():
            public.mkdir(parents=True)
            for asset in baseline['assets']:
                source = self.store.root / 'published-objects' / asset['sha256']
                target = asset_path(public, asset['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)
        report = read_json(self.store.manifest_path(key))
        if not self.publication_readiness(key):
            raise ReportError('Cloudflare 게시 후보는 구현 완료 보고서만 만들 수 있습니다.')
        entries = dict(baseline['reports'])
        entries[key] = with_publication_metadata(
            {field: report[field] for field in ('project', 'title', 'conclusion', 'canonical_project', 'display_name') if field in report},
            baseline['reports'].get(key, {}))
        if key not in baseline['reports']:
            entries[key].update(publication_order=max((publication_order(item) for item in baseline['reports'].values()), default=0) + 1,
                                publication_order_source={'kind': 'verified_commit_sequence'})
        for path in public.glob('projects/*/*/index.html'):
            if re.fullmatch(r'projects/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*/index\.html', path.relative_to(public).as_posix()):
                legacy_key = path.parent.name
                entries.setdefault(legacy_key, {'project': path.parent.parent.name, 'title': '보관 보고서 · ' + legacy_key, 'conclusion': '', **preview_metadata(path)})
        self.store.render(key, publish=True, output_root=public, entries=entries)
        self.store.preflight(public)
        description = self.describe_assets(public)
        previous = {asset['path']: asset for asset in baseline['assets']}
        changed = [asset['path'] for asset in description['assets'] if previous.get(asset['path']) != asset]
        migrations = []
        for asset in description['assets']:
            old = previous.get(asset['path'])
            if old and old != asset and re.fullmatch(r'projects/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*/index\.html', asset['path']) and not asset['path'].startswith(f'projects/{report["project"]}/{key}/'):
                before = (self.store.root / 'published-objects' / old['sha256']).read_bytes()
                after = asset_path(public, asset['path']).read_bytes()
                item = entries[Path(asset['path']).parent.name]
                canonical, _ = catalogue_identity(item)
                href = os.path.relpath(public / 'projects' / canonical / 'index.html', asset_path(public, asset['path']).parent).replace('\\', '/')
                if navigation_migration(before, href) != after:
                    raise ReportError('기존 보고서의 탐색 링크 이외 변경을 거부했습니다.')
                migrations.append({'path': asset['path'], 'before_sha256': old['sha256'], 'after_sha256': asset['sha256'], 'href': href})
        candidate = dict(description, public=str(public), key=key, candidate_id=candidate_id,
            report_type=report['report_type'], kind=report['kind'],
            publication_order_digest=publication_snapshot_digest(entries),
            navigation_migrations=migrations, input_fingerprint=fingerprint, reports=entries, baseline=baseline, changed_paths=changed)
        atomic_json(root / 'candidate.json', candidate)
        return candidate

    def commit_candidate(self, candidate, result):
        if candidate.get('publication_order_digest') != publication_snapshot_digest(candidate['reports']):
            raise ReportError('게시 후보의 고정 발행 순번이 변경되었습니다.')
        public = Path(candidate['public'])
        for asset in candidate['assets']:
            source = self.keep_object(asset_path(public, asset['path']), asset)
            target = asset_path(self.store.public, asset['path'])
            if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != asset['sha256']:
                atomic_copy(source, target)
        atomic_json(self.store.root / 'published.json', {'schema_version': 1,
            'assets': candidate['assets'], 'reports': candidate['reports'],
            'publication_order_schema': 1,
            'publication_order_migration': candidate['baseline'].get('publication_order_migration', {}),
            'asset_digest': result.get('asset_digest', candidate['asset_digest']),
            'version_id': result.get('version_id'), 'checked_at': result.get('checked_at')})

    def journal_path(self, key):
        return self.store.root / 'jobs' / f'{key}.json'

    def report_context(self, key):
        report = read_json(self.store.manifest_path(key))
        if report_key(report) != key:
            raise ReportError('report key와 저장된 보고서 식별자가 일치하지 않습니다.')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', report['project']):
            raise ReportError('저장된 project 경로가 잘못되었습니다.')
        if report.get('goal_id'):
            return {name: report[name] for name in ('thread_id', 'goal_id', 'project')} | {'key': key}
        return {'project': report['project'], 'key': key, 'report_id': key,
                **({'thread_id': report['thread_id']} if report.get('thread_id') else {})}

    def artifact_fingerprint(self, key, public=None):
        """Bind a receipt to inputs, renderer, report assets and deployment target."""
        context = self.report_context(key)
        public = Path(public) if public else self.store.public
        directory = public / 'projects' / context['project'] / key
        paths = {'manifest': self.store.manifest_path(key), 'renderer': Path(__file__),
                 'deployer': Path(__file__).with_name('report-deploy.cjs'),
                 'theme_source': Path(__file__).with_name('report-theme.css'),
                 'editorial_theme_source': Path(__file__).with_name('editorial-theme.css'),
                 'catalogue_theme_source': Path(__file__).with_name('catalogue-theme.css'),
                 'catalogue_theme': public / 'catalogue-theme.css',
                 'theme': public / 'theme.css', 'headers': public / '_headers',
                 'html': directory / 'index.html'}
        paths.update({'font_source/' + name: Path(__file__).with_name('fonts') / name for name in EDITORIAL_FONT_ASSETS})
        paths.update({'shared/' + name: asset_path(public, name) for name in SHARED_FONT_PATHS})
        report = read_json(paths['manifest'])
        for media in report['media']:
            for field in ('path', 'poster'):
                if media.get(field):
                    name = media[field]
                    paths['input/' + name] = source_path(paths['manifest'].parent, name)
        for path in directory.rglob('*'):
            if path.is_file():
                paths['public/' + path.relative_to(directory).as_posix()] = path
        descriptor = public.parent / 'candidate.json'
        if public.resolve().is_relative_to((self.store.root / 'candidates' / key).resolve()) and descriptor.is_file():
            candidate = read_json(descriptor)
            if candidate.get('publication_order_digest') != publication_snapshot_digest(candidate['reports']):
                raise ReportError('게시 후보의 발행 순번 snapshot이 다릅니다.')
            order_metadata = publication_metadata(candidate['reports'].get(key, {}))
        else:
            ledger = self.store.root / 'published.json'
            order_metadata = publication_metadata(read_json(ledger).get('reports', {}).get(key, {})) if ledger.is_file() else {}
        hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(paths.items())}
        target = {name: self.config.get(name) for name in ('base_url', 'account', 'worker_id', 'worker_name', 'access_host', 'wrangler_version')}
        return hashlib.sha256(json.dumps([context, target, hashes, order_metadata], sort_keys=True).encode()).hexdigest()

    def verified_receipt(self, key, public=None):
        path = self.store.root / 'verification' / f'{key}.json'
        try:
            if not self.publication_readiness(key):
                return None
            result = read_json(path)
            context = self.report_context(key)
            expected = self.config['base_url'].rstrip('/') + f'/projects/{context["project"]}/{key}/'
            if (result.get('verified') is True and result.get('url') == expected
                    and all(result.get(name) == value for name, value in context.items())
                    and result.get('artifact_fingerprint') == self.artifact_fingerprint(key, public)):
                return result
        except (OSError, ValueError, TypeError, AttributeError, ReportError):
            pass
        return None

    def excluded_result(self, key):
        """store.lock 안에서 로컬 미리보기만 생성한다. 과거 배포 상태는 바꾸지 않는다."""
        report = read_json(self.store.manifest_path(key))
        local = self.render_local(key)
        return dict(self.report_context(key), report_type=report.get('report_type', 'other'),
                    report_status='excluded', deployment_status='excluded',
                    local_generated=True, html=local.resolve().as_posix(), deployed=False, verified=False,
                    reason='Cloudflare 업로드는 실제 goal에 연결하여 완료 준비를 확인한 구현 완료 보고서만 허용합니다.',
                    final_response_markdown=f'[로컬 보고서](<{local.resolve().as_posix()}>)')

    @staticmethod
    def failure_reason(error):
        if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
            return '배포·검증 시간 초과. 원격 적용 여부가 미확인입니다. publish로 다시 확인하세요.'
        return str(error) if isinstance(error, ReportError) else '보고서 처리 중 로컬 오류. 입력과 검증 도구를 확인하세요.'

    def failure_result(self, key, error):
        context = self.report_context(key)
        local = self.store.root / 'previews' / 'projects' / context['project'] / key / 'index.html'
        generated = local.is_file()
        failure = self.failure_reason(error)
        local_status = '로컬 생성 완료' if generated else '로컬 생성 미완료'
        outcome = getattr(error, 'outcome', {})
        unknown = isinstance(error, (subprocess.TimeoutExpired, TimeoutError)) or outcome.get('deployment_status') == 'unknown'
        deployed = None if unknown else outcome.get('deployed', False)
        status = 'unknown' if unknown else 'applied_unverified' if deployed else 'failed'
        return dict(context, local_generated=generated, html=str(local) if generated else None,
                    deployed=deployed, verified=False, deployment_status=status, error=failure,
                    final_response_markdown=f'보고서: {local_status}, Cloudflare 배포·검증 실패 — {failure}')

    def check_final(self, key, thread_id, goal_id, draft):
        with FileLock(self.store.root / 'store.lock'):
            context = self.report_context(key)
            if not context.get('goal_id') or context.get('thread_id') != thread_id or context['goal_id'] != goal_id:
                raise ReportError('답변 대상 thread/goal과 보고서 식별자가 일치하지 않습니다.')
            result = self.verified_receipt(key)
            if not result:
                raise ReportError('현재 산출물의 배포 검증이 없거나 오래되었습니다. publish가 필요합니다.')
            # Ignore examples/comments: the link must be visible in the final prose.
            prose = re.sub(r'(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$', '', draft)
            prose = re.sub(r'<!--.*?-->|`[^`]*`', '', prose, flags=re.S)
            prose = re.sub(r'(?m)^(?: {4}|\t).*$', '', prose)
            links = re.findall(r'(?<![!\\])\[[^\]\n]+\]\((https://[^\s)]+)\)', prose)
            if result['url'] not in links:
                raise ReportError('최종 답변에 현재 goal의 검증된 Markdown 보고서 링크가 없습니다.')
            return dict(result, final_check='passed', draft_sha256=hashlib.sha256(draft.encode()).hexdigest(),
                        enforcement='explicit-pre-final-check')

    def prepare(self, key):
        if not self.store.manifest_path(key).exists():
            raise ReportError('보고서 입력이 없습니다. goal 완료 전에 reportctl submit이 필요합니다.')
        with FileLock(self.store.root / 'store.lock'):
            context = self.report_context(key)
            report = read_json(self.store.manifest_path(key))
            if not self.publication_readiness(key):
                return self.excluded_result(key)
            self.render_local(key)
            if report.get('evidence_missing'):
                raise ReportError('실제 게임 실행 증거가 누락되어 배포를 보류했습니다.')
            cached = self.verified_receipt(key)
            baseline = self.published_baseline()
            self.candidate = self.build_candidate(key, baseline)
            self.bind_requested_candidate(self.candidate)
            fingerprint = self.artifact_fingerprint(key, self.candidate['public'])
            cached = cached or self.verified_receipt(key, self.candidate['public'])
            route = f'/projects/{report["project"]}/{key}/'
        if cached:
            if self.verifier:
                fresh = self.verifier(Path(self.candidate['public']), route, key, baseline['asset_digest'])
                if fresh.get('verified') is True:
                    self.known_outcome = dict(fresh, deployed=True, deployment_status='applied_unverified')
                    cached = dict(cached, **{name: fresh[name] for name in ('version_id', 'checked_at', 'policy_fingerprint', 'content_status', 'content_verified', 'protection_scope') if name in fresh})
                    with FileLock(self.store.root / 'store.lock'):
                        if fingerprint != self.artifact_fingerprint(key, self.candidate['public']):
                            raise ReportError('원격 재확인 중 보고서 입력이 변경됐습니다. 다시 준비하세요.', dict(fresh, deployed=True, deployment_status='applied_unverified'))
                        self.commit_candidate(self.candidate, fresh)
                        atomic_json(self.store.root / 'verification' / f'{key}.json', cached)
                        return dict(cached, reused_verification=True, remote_rechecked=True)
            else:
                with FileLock(self.store.root / 'store.lock'):
                    self.commit_candidate(self.candidate, cached)
                return dict(cached, reused_verification=True, remote_rechecked=False)
        try:
            result = self.deployer(Path(self.candidate['public']), route, key)
        except Exception as error:
            if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
                raise
            raise ReportError(self.failure_reason(error), getattr(error, 'outcome', {})) from None
        expected = self.config['base_url'].rstrip('/') + route
        if not isinstance(result, dict) or result.get('verified') is not True or result.get('url') != expected:
            raise ReportError('배포 확인 결과 또는 보고서 URL이 일치하지 않습니다.')
        self.known_outcome = dict(result, deployed=True, deployment_status='applied_unverified')
        with FileLock(self.store.root / 'store.lock'):
            if fingerprint != self.artifact_fingerprint(key, self.candidate['public']):
                raise ReportError('배포 중 보고서 산출물이 변경되어 검증을 사용할 수 없습니다.', dict(result, deployed=True, deployment_status='applied_unverified'))
            self.commit_candidate(self.candidate, result)
            result = dict(result, **context, artifact_fingerprint=self.artifact_fingerprint(key),
                          candidate_id=self.candidate['candidate_id'], deployed=True,
                          deployment_status='verified', reused_verification=False,
                          final_response_markdown=f'[{"완료 보고서" if context.get("goal_id") else "보고서"}]({expected})')
            atomic_json(self.store.root / 'verification' / f'{key}.json', result)
        return result

    def retry(self, key):
        # Policy precedes pending recovery, cached verification and any authentication.
        with FileLock(self.store.root / 'store.lock'):
            if not self.publication_readiness(key):
                return self.excluded_result(key)
        with FileLock(self.store.root / 'publish.lock'):
            with FileLock(self.store.root / 'store.lock'):
                if not self.publication_readiness(key):
                    return self.excluded_result(key)
            self.candidate = None
            self.known_outcome = {}
            pending_path = self.store.root / 'pending-deployment.json'
            if pending_path.exists():
                pending = read_json(pending_path)
                if self.requested_publication is not None and pending['key'] != key:
                    raise ReportError('이번 명시 게시로 다른 보고서의 미확인 배포를 조회하지 않습니다.', {'deployed': None, 'deployment_status': 'unknown'})
                descriptor = read_json(Path(pending['candidate']))
                if pending['key'] != key or descriptor['input_fingerprint'] != self.input_fingerprint(key):
                    if self.requested_publication is not None:
                        raise ReportError('미확인 후보의 입력·대상이 현재 명시 게시 요청과 다릅니다. 기존 후보를 먼저 복구하세요.', {'deployed': None, 'deployment_status': 'unknown'})
                    self.resolve_pending(pending)
            path = self.journal_path(key)
            job = read_json(path) if path.exists() else {'key': key}
            if self.requested_publication is not None and pending_path.exists():
                self.known_outcome = {'deployment_status': job.get('deployment_status', 'unknown'), 'deployed': job.get('deployed')}
            try:
                result = self.prepare(key)
            except Exception as error:
                if not getattr(error, 'outcome', {}) and self.known_outcome:
                    error = ReportError(self.failure_reason(error), self.known_outcome)
                failure = self.failure_result(key, error)
                with FileLock(self.store.root / 'store.lock'):
                    job.update(report='failed', failure=self.failure_reason(error), deployment_status=failure['deployment_status'], deployed=failure['deployed'])
                    if job.get('url'):
                        job['last_verified_url'] = job.pop('url')
                    outcome = getattr(error, 'outcome', {})
                    job.update({name: outcome[name] for name in ('phase', 'version_id', 'deployment_id', 'asset_digest') if name in outcome})
                    if self.candidate:
                        job['candidate'] = str(Path(self.candidate['public']).parent / 'candidate.json')
                        if failure['deployed'] is not False:
                            atomic_json(pending_path, {'key': key, 'candidate': job['candidate']})
                    atomic_json(path, job)
                raise ReportError(job['failure'], failure | getattr(error, 'outcome', {})) from None
            if result.get('report_status') == 'excluded':
                return result
            with FileLock(self.store.root / 'store.lock'):
                job.update(report='ready', url=result['url'], deployment=result.get('version_id'), deployment_status='verified', deployed=True)
                job.pop('failure', None)
                atomic_json(path, job)
                if pending_path.exists() and read_json(pending_path)['key'] == key:
                    pending_path.unlink()
            return result

    def resolve_pending(self, pending):
        if not self.completion_readiness(pending['key']):
            raise ReportError('이전 게시 후보의 goal 완료 준비가 유효하지 않습니다. 원격 확인을 수행하지 않습니다.')
        if not self.verifier:
            raise ReportError('이전 배포의 원격 적용 여부가 미확인입니다. 해당 보고서를 먼저 재확인하세요.', {'deployed': None, 'deployment_status': 'unknown'})
        descriptor = Path(pending['candidate']).resolve()
        if not descriptor.is_relative_to((self.store.root / 'candidates').resolve()):
            raise ReportError('이전 게시 후보 기록의 경로가 잘못되었습니다.')
        candidate = read_json(descriptor)
        item = candidate['reports'][pending['key']]
        route = f'/projects/{item["project"]}/{pending["key"]}/'
        previous_candidate = self.candidate
        self.candidate = candidate
        try:
            result = self.verifier(Path(candidate['public']), route, pending['key'], candidate['asset_digest'])
        finally:
            self.candidate = previous_candidate
        with FileLock(self.store.root / 'store.lock'):
            if result.get('verified') is True:
                self.commit_candidate(candidate, result)
                job_path = self.journal_path(pending['key'])
                job = read_json(job_path)
                job.update(deployment_status='verified', deployed=True, recovered_before_next_publish=True,
                           version_id=result.get('version_id'))
                atomic_json(job_path, job)
            elif result.get('active_asset_digest') != candidate['baseline']['asset_digest']:
                raise ReportError('이전 후보 또는 게시 기준과 다른 원격 버전입니다. 자동 게시를 보류합니다.', {'deployed': None, 'deployment_status': 'unknown'})
            (self.store.root / 'pending-deployment.json').unlink()


def load_config(path=None):
    target = Path(path or os.environ.get('CODEX_REPORT_CONFIG') or Path.home() / '.codex' / 'goal-report' / 'settings.json')
    if not target.exists():
        return None
    config = read_json(target)
    if not isinstance(config, dict):
        raise ReportError('보고서 설정은 JSON 객체여야 합니다.')
    if isinstance(config.get('config_path'), str):
        config = read_json(Path(config['config_path']))
    if not isinstance(config, dict):
        raise ReportError('보고서 설정은 JSON 객체여야 합니다.')
    if not config.get('enabled'):
        return None
    if not isinstance(config.get('root'), str) or not Path(config['root']).is_absolute():
        raise ReportError('보고서 저장소의 절대 경로가 필요합니다.')
    parsed = urlparse(config.get('base_url', ''))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise ReportError('승인된 production 보고서 주소가 필요합니다.')
    return config
