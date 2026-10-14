"""Bounded metadata-only checks; transport failures remain reportable findings."""
import json
import os
from datetime import datetime, timezone
from urllib.request import urlopen


def diagnose(health, now):
    problems = []
    if not isinstance(health, dict) or health.get('status') != 'ok':
        return ['health 응답이 정상 객체가 아닙니다.']
    for field, minutes in [('live_stored_at', 30), ('odds_generated_at', 90),
                           ('picks_generated_at', 90)]:
        try:
            stamp = datetime.fromisoformat(str(health[field]).replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('timezone missing')
            age = (now - stamp).total_seconds() / 60
            if age < -5:
                problems.append(f'{field}: 미래 시각입니다.')
            elif age >= minutes:
                problems.append(f'{field}: {int(age)}분 지연 (경보 {minutes}분).')
        except (KeyError, ValueError, TypeError):
            problems.append(f'{field}: 생성/저장 시각을 확인할 수 없습니다.')
    free = health.get('disk_free_mb')
    if not isinstance(free, (int, float)) or isinstance(free, bool):
        problems.append('디스크 여유를 확인할 수 없습니다.')
    elif free < 300:
        problems.append(f'디스크 여유 {free}MB: 300MB 미만입니다.')
    return problems


def check(url, opener=urlopen, now=None):
    try:
        with opener(url.rstrip('/') + '/health', timeout=25) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('oversized health')
        return diagnose(json.loads(raw), now or datetime.now(timezone.utc))
    except Exception as exc:
        return [f'health 조회 실패: {type(exc).__name__}']


def main():
    problems = check(os.environ.get('APP_URL', 'https://proto-odds-collector.fly.dev'))
    body = '\n'.join('- ' + p for p in problems)
    print(body or 'DB 수집·판정 시각과 디스크 여유 정상')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
            handle.write(f'ok={str(not problems).lower()}\nproblems<<WATCHDOG_END\n{body}\nWATCHDOG_END\n')


if __name__ == '__main__':
    main()
