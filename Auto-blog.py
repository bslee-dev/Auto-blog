"""
WordPress Auto Poster v2
마크다운 파일을 감시하다가 자동으로 워드프레스에 포스팅

마크다운 형식:
---
title: 포스트 제목
category: 카테고리명
date: 2026-01-15 18:00
status: publish / draft / future
---

본문 내용...

사용법:
1. .env 파일에 설정 채우고
2. pip install watchdog markdown requests pyyaml python-dotenv
3. python wp_auto_poster.py
"""

import os
import re
import time
import shutil
import requests
import markdown
import yaml
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from watchdog.observers.polling import PollingObserver as Observer
from watchdog.events import FileSystemEventHandler

# .env 파일 로드
load_dotenv()

# ============ 설정 (.env에서 로드) ============
WP_URL = os.getenv('WP_URL')
WP_USER = os.getenv('WP_USER')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD')
WATCH_FOLDER = os.getenv('WATCH_FOLDER')
PUBLISHED_FOLDER = os.getenv('PUBLISHED_FOLDER')
# =============================================


def get_category_id(category_name):
    """카테고리 이름으로 ID 찾기. 없으면 None 반환"""
    if not category_name:
        return None

    api_url = f"{WP_URL}/wp-json/wp/v2/categories"
    response = requests.get(api_url, params={'search': category_name})

    if response.status_code == 200:
        categories = response.json()
        for cat in categories:
            if cat['name'].lower() == category_name.lower():
                return cat['id']
    return None


def parse_frontmatter(content):
    """마크다운에서 YAML 프론트매터 파싱"""
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
    match = re.match(pattern, content, re.DOTALL)

    if match:
        try:
            metadata = yaml.safe_load(match.group(1))
            body = match.group(2)
            return metadata or {}, body
        except yaml.YAMLError:
            return {}, content
    return {}, content


class MarkdownHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith('.md'):
            # 파일 쓰기 완료될 때까지 잠깐 대기
            time.sleep(1)
            self.post_to_wordpress(event.src_path)

    def post_to_wordpress(self, filepath):
        filepath = Path(filepath)
        print(f"\n📄 새 파일 감지: {filepath.name}")

        # 파일 읽기
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # 프론트매터 파싱
        metadata, body = parse_frontmatter(content)

        # 제목: 메타데이터 > 첫 번째 # 헤더 > 파일명
        title = metadata.get('title')
        if not title:
            lines = body.split('\n')
            for line in lines:
                if line.startswith('# '):
                    title = line[2:].strip()
                    body = body.replace(line + '\n', '', 1)
                    break
        if not title:
            title = filepath.stem

        # 상태 (기본값: draft)
        status = metadata.get('status', 'draft').lower()
        if status not in ['publish', 'draft', 'future']:
            status = 'draft'

        # 날짜 처리
        post_date = metadata.get('date')
        date_str = None
        if post_date:
            if isinstance(post_date, datetime):
                date_str = post_date.strftime('%Y-%m-%dT%H:%M:%S')
            elif isinstance(post_date, str):
                try:
                    parsed = datetime.strptime(post_date, '%Y-%m-%d %H:%M')
                    date_str = parsed.strftime('%Y-%m-%dT%H:%M:%S')
                except ValueError:
                    try:
                        parsed = datetime.strptime(post_date, '%Y-%m-%d')
                        date_str = parsed.strftime('%Y-%m-%dT%H:%M:%S')
                    except ValueError:
                        print(f"⚠️  날짜 형식 오류: {post_date} (무시됨)")

        # 카테고리
        category_name = metadata.get('category')
        category_id = get_category_id(category_name)
        if category_name and not category_id:
            print(f"⚠️  카테고리 '{category_name}' 없음 → 미분류로 등록")

        # 마크다운 → HTML 변환
        html_content = markdown.markdown(
            body,
            extensions=['fenced_code', 'tables', 'nl2br']
        )

        # WordPress API 요청 데이터
        post_data = {
            'title': title,
            'content': html_content,
            'status': status
        }

        if date_str:
            post_data['date'] = date_str

        if category_id:
            post_data['categories'] = [category_id]

        # WordPress API 호출
        api_url = f"{WP_URL}/wp-json/wp/v2/posts"

        response = requests.post(
            api_url,
            auth=(WP_USER, WP_APP_PASSWORD),
            json=post_data
        )

        if response.status_code == 201:
            post_data = response.json()
            post_url = post_data.get('link', '')

            status_msg = {
                'publish': '발행완료',
                'draft': '임시저장',
                'future': '예약발행'
            }.get(status, status)

            print(f"✅ 포스팅 성공! ({status_msg})")
            print(f"   제목: {title}")
            if category_name and category_id:
                print(f"   카테고리: {category_name}")
            if date_str and status == 'future':
                print(f"   예약시간: {date_str}")
            print(f"   URL: {post_url}")

            # published 폴더로 이동
            os.makedirs(PUBLISHED_FOLDER, exist_ok=True)
            dest = Path(PUBLISHED_FOLDER) / filepath.name
            shutil.move(str(filepath), str(dest))
            print(f"   파일 이동: {dest}")
        else:
            print(f"❌ 포스팅 실패: {response.status_code}")
            print(f"   에러: {response.text}")


def process_existing_files(handler):
    """시작 시 폴더에 있는 기존 .md 파일 처리"""
    existing_files = list(Path(WATCH_FOLDER).glob('*.md'))
    if existing_files:
        print(f"\n📂 기존 파일 {len(existing_files)}개 발견")
        for filepath in existing_files:
            handler.post_to_wordpress(str(filepath))


def main():
    # 설정 확인
    if not all([WP_URL, WP_USER, WP_APP_PASSWORD, WATCH_FOLDER, PUBLISHED_FOLDER]):
        print("❌ .env 파일 설정을 확인하세요!")
        return

    # 폴더 없으면 생성
    os.makedirs(WATCH_FOLDER, exist_ok=True)
    os.makedirs(PUBLISHED_FOLDER, exist_ok=True)

    print("=" * 50)
    print("🚀 WordPress Auto Poster v2 시작")
    print(f"   감시 폴더: {WATCH_FOLDER}")
    print(f"   발행 후 이동: {PUBLISHED_FOLDER}")
    print("   종료하려면 Ctrl+C")
    print("=" * 50)
    print("\n📝 마크다운 형식:")
    print("   ---")
    print("   title: 제목")
    print("   category: 카테고리명")
    print("   date: 2026-01-15 18:00")
    print("   status: publish / draft / future")
    print("   ---")
    print("=" * 50)

    event_handler = MarkdownHandler()

    # 기존 파일 먼저 처리
    process_existing_files(event_handler)

    observer = Observer()
    observer.schedule(event_handler, WATCH_FOLDER, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n👋 종료됨")

    observer.join()


if __name__ == "__main__":
    main()