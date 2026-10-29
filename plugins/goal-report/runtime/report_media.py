"""Local explicit media processing. Originals are never overwritten or removed."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from fractions import Fraction
from pathlib import Path

LIMIT = 24 * 1024 * 1024


def binary(name):
    override = os.environ.get('CODEX_REPORT_' + name.upper())
    from goal_report import load_config
    settings = load_config() or {}
    path = override or settings.get(name) or shutil.which(name)
    if not path:
        raise RuntimeError(name + ' 실행 경로를 패키지 설정에 지정하세요.')
    if not Path(path).is_file():
        raise RuntimeError(f'{name} 실행 파일이 없습니다. 기존 로컬 실행 파일 경로를 설정하세요.')
    return path


def run(command, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('로컬 미디어 도구 실행 실패. 입력·인코더·장면 길이를 확인하세요.')
    return result.stdout, result.stderr


def inspect_media(path):
    output, _ = run([binary('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)])
    data = json.loads(output)
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    if video is None:
        raise RuntimeError('실행 영상 트랙이 없습니다.')
    return {
        'video': {k: video.get(k) for k in ('codec_name', 'profile', 'width', 'height', 'pix_fmt', 'avg_frame_rate')},
        'duration': float(data['format']['duration']),
        'bytes': Path(path).stat().st_size,
        'audio': any(s['codec_type'] == 'audio' for s in data['streams']),
        'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def fit_dimensions(width, height):
    cap_w, cap_h = (1920, 1080) if width >= height else (1080, 1920)
    ratio = min(1.0, cap_w / width, cap_h / height)
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def encode(source, destination, codec='h264', start=0., seconds=None, movement=False, remove_audio=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise RuntimeError('원본 또는 기존 출력 파일을 덮어쓰지 않습니다. 새 출력 경로를 선택하세요.')
    original = inspect_media(source)
    if not math.isfinite(start) or start < 0 or start >= original['duration']:
        raise RuntimeError('유효한 장면 시작 시간이 필요합니다.')
    duration = original['duration'] - start if seconds is None else min(seconds, original['duration'] - start)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError('유효한 장면 길이가 필요합니다.')
    w, h = fit_dimensions(original['video']['width'], original['video']['height'])
    source_fps = float(Fraction(original['video']['avg_frame_rate']))
    fps = min(source_fps, 60 if movement else 30)
    filters = f'scale={w}:{h},fps={fps:.6f},format=yuv420p'
    audio_bitrate = 128000 if original['audio'] and not remove_audio else 0
    desired_bitrate = int(w * h * fps * .12)
    budget_bitrate = int(LIMIT * .92 * 8 / duration) - audio_bitrate
    bitrate = min(desired_bitrate, budget_bitrate)
    if bitrate < min(2_000_000, int(w * h * fps * .04)):
        raise RuntimeError('24 MiB 안에서 가독성을 유지하기 어렵습니다. 의미 있는 더 짧은 장면으로 나누세요.')
    command = [binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-ss', str(start), '-i', str(source), '-t', str(duration), '-map', '0:v:0', '-vf', filters]
    if remove_audio or not original['audio']:
        command += ['-an']
    else:
        command += ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', '128k']
    if codec == 'h264':
        command += ['-c:v', 'libopenh264', '-b:v', str(bitrate), '-profile:v', 'high', '-threads', '2']
    elif codec == 'av1':
        command += ['-c:v', 'libsvtav1', '-crf', '28', '-preset', '8', '-svtav1-params', 'lp=2']
    else:
        raise RuntimeError('codec은 h264 또는 av1입니다.')
    command += ['-movflags', '+faststart', str(destination)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    run(command)
    elapsed = time.monotonic() - began
    actual = inspect_media(destination)
    if actual['bytes'] > LIMIT or actual['video']['width'] > w or actual['video']['height'] > h:
        raise RuntimeError('실제 출력이 제한을 초과했습니다. 원본과 출력은 유지했습니다. 더 짧은 장면을 선택하세요.')
    run([binary('ffmpeg'), '-v', 'error', '-nostdin', '-i', str(destination), '-f', 'null', '-'])
    if codec == 'h264' and actual['video']['codec_name'] != 'h264':
        raise RuntimeError('실제 코덱이 요청과 일치하지 않습니다.')
    data = destination.read_bytes()
    if data.find(b'moov') < 0 or data.find(b'moov') > data.find(b'mdat'):
        raise RuntimeError('빠른 재생을 위한 MP4 faststart가 적용되지 않았습니다.')
    return dict(actual, codec=codec, encode_seconds=round(elapsed, 3), source_dimensions=[original['video']['width'], original['video']['height']], source_fps=source_fps, output_fps=fps, scene_start=start, scene_seconds=duration, original_preserved=True, audio_removed=original['audio'] and remove_audio, decode_passed=True, faststart=True, device_compatibility='미검증: 실제 iPhone Safari와 Windows 기기 확인 필요')


def image(source, destination):
    from PIL import Image
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise RuntimeError('원본 또는 기존 출력 이미지를 덮어쓰지 않습니다.')
    with Image.open(source) as raw:
        original = raw.size
        cap = (1920, 1080) if raw.width >= raw.height else (1080, 1920)
        raw.thumbnail(cap, Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw.convert('RGB').save(destination)
    if destination.stat().st_size > LIMIT:
        raise RuntimeError('이미지가 24 MiB를 초과합니다.')
    return {'source_dimensions': original, 'output_dimensions': raw.size, 'original_preserved': True}


def compare(source, output, start=0., seconds=6., movement=False):
    output = Path(output)
    if not 0 < seconds <= 10:
        raise RuntimeError('대표 장면 비교는 10초 이하로 실행하세요. 전체 검증 영상은 별도 장면으로 처리하세요.')
    info = inspect_media(source)
    w, h = fit_dimensions(info['video']['width'], info['video']['height'])
    fps = min(float(Fraction(info['video']['avg_frame_rate'])), 60 if movement else 30)
    result = {'source_sha256': info['sha256'], 'source_dimensions': [info['video']['width'], info['video']['height']], 'conditions': f'동일 {start}초 시작/{seconds}초 장면, {w}×{h}, {fps:g}fps, CPU 최대2스레드, 원본 오디오 보존', 'compatibility': '실제 iPhone/Windows 재생 미검증. AV1로 전환하지 않고 H.264를 잠정 기본값으로 유지.', 'outputs': []}
    for codec in ('h264', 'av1'):
        file = output / f'comparison-{codec}.mp4'
        measured = encode(source, file, codec, start, seconds, movement)
        _, stderr = run([binary('ffmpeg'), '-hide_banner', '-nostdin', '-ss', str(start), '-t', str(seconds), '-i', str(source), '-i', str(file), '-filter_complex', f'[0:v]scale={w}:{h},fps={fps},format=yuv420p,setpts=PTS-STARTPTS[ref];[1:v]setpts=PTS-STARTPTS[test];[ref][test]ssim', '-an', '-f', 'null', '-'])
        import re
        match = re.search(r'All:([0-9.]+)', stderr)
        if not match:
            raise RuntimeError('SSIM 품질 비교 결과가 없습니다.')
        measured.update(filename=file.name, ssim=float(match[1]))
        result['outputs'].append(measured)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description='명시적으로 선택한 로컬 미디어 처리; 원본 유지')
    parser.add_argument('action', choices=['inspect', 'encode', 'compare', 'image'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--codec', choices=['h264', 'av1'], default='h264')
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--seconds', type=float)
    parser.add_argument('--movement', action='store_true')
    parser.add_argument('--remove-audio', action='store_true')
    args = parser.parse_args()
    if args.action == 'inspect':
        result = inspect_media(args.source)
    elif not args.output:
        parser.error('--output이 필요합니다.')
    elif args.action == 'image':
        result = image(args.source, args.output)
    elif args.action == 'compare':
        result = compare(args.source, args.output, args.start, args.seconds or 6, args.movement)
    else:
        result = encode(args.source, args.output, args.codec, args.start, args.seconds, args.movement, args.remove_audio)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
