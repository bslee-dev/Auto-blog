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
3. python Auto-blog.py
"""

import os
import re
import time
import shutil
import logging
import requests
import markdown
import yaml
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
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

# ============ 로깅 설정 ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
# ===================================

# ============ 전역 변수 ============
# 처리된 파일 추적 (중복 방지)
processed_files = set()
# ===================================


def get_category_id(category_name):
    """카테고리 이름으로 ID 찾기. 없으면 None 반환 (정확한 매칭)"""
    if not category_name:
        return None

    try:
        # 모든 카테고리 가져오기 (정확한 매칭을 위해)
        api_url = f"{WP_URL}/wp-json/wp/v2/categories"
        response = requests.get(
            api_url,
            params={'per_page': 100, 'orderby': 'name', 'order': 'asc'},
            auth=(WP_USER, WP_APP_PASSWORD),
            timeout=10
        )

        if response.status_code == 200:
            categories = response.json()
            category_name_lower = category_name.lower()
            for cat in categories:
                if cat['name'].lower() == category_name_lower:
                    return cat['id']
        else:
            logger.warning(f"카테고리 조회 실패: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"카테고리 조회 중 네트워크 오류: {e}")
    except Exception as e:
        logger.error(f"카테고리 조회 중 오류: {e}")
    
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


def wait_for_file_ready(filepath, max_retries=10, retry_delay=0.5):
    """파일이 완전히 쓰여졌는지 확인 (재시도 로직)"""
    filepath = Path(filepath)
    last_size = 0
    
    for attempt in range(max_retries):
        try:
            if not filepath.exists():
                time.sleep(retry_delay)
                continue
            
            current_size = filepath.stat().st_size
            if current_size == last_size and current_size > 0:
                # 파일 크기가 변하지 않으면 쓰기 완료로 간주
                time.sleep(retry_delay)  # 마지막 안전 대기
                return True
            last_size = current_size
            time.sleep(retry_delay)
        except (OSError, PermissionError) as e:
            logger.debug(f"파일 확인 중 오류 (시도 {attempt + 1}/{max_retries}): {e}")
            time.sleep(retry_delay)
    
    return False


class MarkdownHandler(FileSystemEventHandler):
    def __init__(self):
        self.processing = set()  # 현재 처리 중인 파일 추적
    
    def on_created(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith('.md'):
            filepath = Path(event.src_path)
            # 중복 처리 방지
            if str(filepath) in processed_files or str(filepath) in self.processing:
                logger.debug(f"이미 처리된 파일 무시: {filepath.name}")
                return
            
            # 파일 쓰기 완료 대기
            if wait_for_file_ready(filepath):
                self.post_to_wordpress(event.src_path)
            else:
                logger.warning(f"파일 준비 대기 시간 초과: {filepath.name}")
    
    def on_modified(self, event):
        """파일 수정 시에도 처리 (선택적)"""
        if event.is_directory:
            return
        if event.src_path.endswith('.md'):
            filepath = Path(event.src_path)
            # published 폴더에 있으면 무시
            if PUBLISHED_FOLDER and str(filepath).startswith(str(Path(PUBLISHED_FOLDER))):
                return
            
            # 이미 처리된 파일은 수정 시 재처리하지 않음 (중복 방지)
            if str(filepath) in processed_files:
                logger.debug(f"이미 처리된 파일 수정 무시: {filepath.name}")
                return
            
            if str(filepath) not in self.processing:
                if wait_for_file_ready(filepath):
                    self.post_to_wordpress(event.src_path)

    def post_to_wordpress(self, filepath):
        filepath = Path(filepath)
        filepath_str = str(filepath)
        
        # 중복 처리 방지
        if filepath_str in processed_files or filepath_str in self.processing:
            logger.debug(f"이미 처리 중이거나 처리된 파일: {filepath.name}")
            return
        
        self.processing.add(filepath_str)
        logger.info(f"📄 새 파일 감지: {filepath.name}")

        try:
            # 파일 읽기
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except FileNotFoundError:
                logger.error(f"파일을 찾을 수 없음: {filepath}")
                self.processing.discard(filepath_str)
                return
            except PermissionError:
                logger.error(f"파일 읽기 권한 없음: {filepath}")
                self.processing.discard(filepath_str)
                return
            except Exception as e:
                logger.error(f"파일 읽기 오류: {filepath} - {e}")
                self.processing.discard(filepath_str)
                return

            if not content.strip():
                logger.warning(f"빈 파일 무시: {filepath.name}")
                self.processing.discard(filepath_str)
                return

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
                            logger.warning(f"날짜 형식 오류: {post_date} (무시됨)")

            # 카테고리
            category_name = metadata.get('category')
            category_id = get_category_id(category_name)
            if category_name and not category_id:
                logger.warning(f"카테고리 '{category_name}' 없음 → 미분류로 등록")

            # 마크다운 → HTML 변환
            try:
                html_content = markdown.markdown(
                    body,
                    extensions=['fenced_code', 'tables', 'nl2br']
                )
            except Exception as e:
                logger.error(f"마크다운 변환 오류: {e}")
                self.processing.discard(filepath_str)
                return

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

            try:
                response = requests.post(
                    api_url,
                    auth=(WP_USER, WP_APP_PASSWORD),
                    json=post_data,
                    timeout=30
                )
            except requests.exceptions.Timeout:
                logger.error(f"API 요청 시간 초과: {filepath.name}")
                self.processing.discard(filepath_str)
                return
            except requests.exceptions.ConnectionError:
                logger.error(f"네트워크 연결 오류: {filepath.name}")
                self.processing.discard(filepath_str)
                return
            except requests.exceptions.RequestException as e:
                logger.error(f"API 요청 오류: {filepath.name} - {e}")
                self.processing.discard(filepath_str)
                return

            if response.status_code == 201:
                post_response = response.json()
                post_url = post_response.get('link', '')

                status_msg = {
                    'publish': '발행완료',
                    'draft': '임시저장',
                    'future': '예약발행'
                }.get(status, status)

                logger.info(f"✅ 포스팅 성공! ({status_msg})")
                logger.info(f"   제목: {title}")
                if category_name and category_id:
                    logger.info(f"   카테고리: {category_name}")
                if date_str and status == 'future':
                    logger.info(f"   예약시간: {date_str}")
                logger.info(f"   URL: {post_url}")

                # published 폴더로 이동
                try:
                    os.makedirs(PUBLISHED_FOLDER, exist_ok=True)
                    dest = Path(PUBLISHED_FOLDER) / filepath.name
                    
                    # 목적지에 같은 이름의 파일이 있으면 백업
                    if dest.exists():
                        backup_name = f"{dest.stem}_{int(time.time())}{dest.suffix}"
                        backup_path = dest.parent / backup_name
                        shutil.move(str(dest), str(backup_path))
                        logger.info(f"   기존 파일 백업: {backup_name}")
                    
                    shutil.move(str(filepath), str(dest))
                    logger.info(f"   파일 이동: {dest}")
                    
                    # 처리 완료 표시
                    processed_files.add(filepath_str)
                except (OSError, PermissionError, shutil.Error) as e:
                    logger.error(f"파일 이동 실패: {filepath} → {dest} - {e}")
                    # 포스팅은 성공했으므로 처리 완료로 표시
                    processed_files.add(filepath_str)
            else:
                logger.error(f"❌ 포스팅 실패: {response.status_code}")
                try:
                    error_data = response.json()
                    error_msg = error_data.get('message', response.text)
                    logger.error(f"   에러: {error_msg}")
                except:
                    logger.error(f"   에러: {response.text}")
        
        except Exception as e:
            logger.error(f"예상치 못한 오류 발생: {filepath.name} - {e}", exc_info=True)
        finally:
            self.processing.discard(filepath_str)


def process_existing_files(handler):
    """시작 시 폴더에 있는 기존 .md 파일 처리"""
    try:
        existing_files = list(Path(WATCH_FOLDER).glob('*.md'))
        if existing_files:
            logger.info(f"\n📂 기존 파일 {len(existing_files)}개 발견")
            for filepath in existing_files:
                handler.post_to_wordpress(str(filepath))
    except Exception as e:
        logger.error(f"기존 파일 처리 중 오류: {e}")


def validate_config():
    """설정 검증"""
    errors = []
    
    # 필수 설정 확인
    if not WP_URL:
        errors.append("WP_URL이 설정되지 않았습니다")
    elif not WP_URL.startswith(('http://', 'https://')):
        errors.append("WP_URL은 http:// 또는 https://로 시작해야 합니다")
    else:
        try:
            parsed = urlparse(WP_URL)
            if not parsed.netloc:
                errors.append("WP_URL 형식이 올바르지 않습니다")
        except Exception:
            errors.append("WP_URL 형식이 올바르지 않습니다")
    
    if not WP_USER:
        errors.append("WP_USER이 설정되지 않았습니다")
    
    if not WP_APP_PASSWORD:
        errors.append("WP_APP_PASSWORD가 설정되지 않았습니다")
    
    if not WATCH_FOLDER:
        errors.append("WATCH_FOLDER가 설정되지 않았습니다")
    else:
        watch_path = Path(WATCH_FOLDER)
        try:
            watch_path.mkdir(parents=True, exist_ok=True)
            if not watch_path.is_dir():
                errors.append(f"WATCH_FOLDER가 디렉토리가 아닙니다: {WATCH_FOLDER}")
        except Exception as e:
            errors.append(f"WATCH_FOLDER 생성/접근 실패: {WATCH_FOLDER} - {e}")
    
    if not PUBLISHED_FOLDER:
        errors.append("PUBLISHED_FOLDER가 설정되지 않았습니다")
    else:
        published_path = Path(PUBLISHED_FOLDER)
        try:
            published_path.mkdir(parents=True, exist_ok=True)
            if not published_path.is_dir():
                errors.append(f"PUBLISHED_FOLDER가 디렉토리가 아닙니다: {PUBLISHED_FOLDER}")
        except Exception as e:
            errors.append(f"PUBLISHED_FOLDER 생성/접근 실패: {PUBLISHED_FOLDER} - {e}")
    
    # WATCH_FOLDER와 PUBLISHED_FOLDER가 같으면 안 됨
    if WATCH_FOLDER and PUBLISHED_FOLDER:
        if Path(WATCH_FOLDER).resolve() == Path(PUBLISHED_FOLDER).resolve():
            errors.append("WATCH_FOLDER와 PUBLISHED_FOLDER는 같은 폴더일 수 없습니다")
    
    return errors


def test_wordpress_connection():
    """WordPress 연결 테스트"""
    try:
        api_url = f"{WP_URL}/wp-json/wp/v2/posts"
        response = requests.get(
            api_url,
            auth=(WP_USER, WP_APP_PASSWORD),
            params={'per_page': 1},
            timeout=10
        )
        if response.status_code == 200:
            logger.info("✅ WordPress 연결 성공")
            return True
        elif response.status_code == 401:
            logger.error("❌ WordPress 인증 실패 (사용자명 또는 앱 비밀번호 확인)")
            return False
        else:
            logger.warning(f"⚠️  WordPress 연결 응답: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ WordPress 연결 실패: {e}")
        return False


def main():
    # 설정 검증
    config_errors = validate_config()
    if config_errors:
        logger.error("❌ 설정 오류:")
        for error in config_errors:
            logger.error(f"   - {error}")
        return

    # WordPress 연결 테스트
    if not test_wordpress_connection():
        logger.warning("⚠️  WordPress 연결에 문제가 있습니다. 계속 진행합니다...")

    logger.info("=" * 50)
    logger.info("🚀 WordPress Auto Poster v2 시작")
    logger.info(f"   감시 폴더: {WATCH_FOLDER}")
    logger.info(f"   발행 후 이동: {PUBLISHED_FOLDER}")
    logger.info("   종료하려면 Ctrl+C")
    logger.info("=" * 50)
    logger.info("\n📝 마크다운 형식:")
    logger.info("   ---")
    logger.info("   title: 제목")
    logger.info("   category: 카테고리명")
    logger.info("   date: 2026-01-15 18:00")
    logger.info("   status: publish / draft / future")
    logger.info("   ---")
    logger.info("=" * 50)

    event_handler = MarkdownHandler()

    # 기존 파일 먼저 처리
    process_existing_files(event_handler)

    observer = Observer()
    try:
        observer.schedule(event_handler, WATCH_FOLDER, recursive=False)
        observer.start()
        logger.info("👀 파일 감시 시작...")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n👋 종료 신호 수신...")
    except Exception as e:
        logger.error(f"감시자 시작 오류: {e}")
    finally:
        observer.stop()
        observer.join()
        logger.info("종료 완료")


if __name__ == "__main__":
    main()