import os
import re
import json
import logging
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8581907820:AAEJr293-EHYnj-gZwY4owf6lmHrOI-Cl1w")
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Cloud Run: 환경변수에서 토큰 읽기, 로컬: 파일에서 읽기
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")


def get_token_data():
    """token.json 데이터 반환 (환경변수 우선, 없으면 파일)"""
    env_token = os.environ.get("GOOGLE_TOKEN_JSON")
    if env_token:
        return json.loads(env_token)
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save_token_data(creds):
    """갱신된 토큰 저장"""
    env_token = os.environ.get("GOOGLE_TOKEN_JSON")
    if not env_token:
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())


def get_calendar_service():
    """Google Calendar API 서비스 객체 반환"""
    token_data = get_token_data()
    creds = Credentials.from_authorized_user_info(token_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token_data(creds)
    return build("calendar", "v3", credentials=creds)


def parse_datetime(text):
    """자연어에서 날짜/시간 파싱"""
    now = datetime.now()
    year = now.year
    month = now.month
    day = now.day
    hour = None
    minute = 0
    all_day = False

    if "모레" in text:
        target = now + timedelta(days=2)
        year, month, day = target.year, target.month, target.day
    elif "내일" in text:
        target = now + timedelta(days=1)
        year, month, day = target.year, target.month, target.day
    elif "오늘" in text:
        pass

    weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
    for wd_name, wd_num in weekdays.items():
        if wd_name in text:
            days_ahead = wd_num - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target = now + timedelta(days=days_ahead)
            year, month, day = target.year, target.month, target.day
            break

    if "다음주" in text or "다음 주" in text:
        for wd_name, wd_num in weekdays.items():
            if wd_name in text:
                days_ahead = wd_num - now.weekday() + 7
                target = now + timedelta(days=days_ahead)
                year, month, day = target.year, target.month, target.day
                break

    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        if month < now.month or (month == now.month and day < now.day):
            year += 1

    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))

    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m and not re.search(r"\d{4}[-./]", text):
        month = int(m.group(1))
        day = int(m.group(2))

    pm = "오후" in text or "저녁" in text or "밤" in text
    am = "오전" in text or "아침" in text

    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})시\s*(\d{1,2})분", text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
        else:
            m = re.search(r"(\d{1,2})시", text)
            if m:
                hour = int(m.group(1))
            m2 = re.search(r"(\d{1,2})분", text)
            if m2 and hour is not None:
                minute = int(m2.group(1))

    if hour is not None:
        if pm and hour < 12:
            hour += 12
        elif am and hour == 12:
            hour = 0
    else:
        all_day = True

    if all_day:
        start_date = datetime(year, month, day)
        return start_date, None, True
    else:
        start_dt = datetime(year, month, day, hour, minute)
        end_dt = start_dt + timedelta(hours=1)
        return start_dt, end_dt, False


def extract_title(text):
    """메시지에서 일정 제목 추출"""
    title = text.strip()
    patterns = [
        r"\d{4}[-./]\d{1,2}[-./]\d{1,2}",
        r"\d{1,2}월\s*\d{1,2}일",
        r"\d{1,2}/\d{1,2}",
        r"오전|오후|아침|저녁|밤",
        r"\d{1,2}시\s*\d{1,2}분",
        r"\d{1,2}시",
        r"\d{1,2}:\d{2}",
        r"오늘|내일|모레",
        r"다음\s*주",
        r"월요일|화요일|수요일|목요일|금요일|토요일|일요일",
        r"\d{1,2}분",
    ]
    for p in patterns:
        title = re.sub(p, "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title if title else "일정"


def create_calendar_event(title, start_dt, end_dt, all_day):
    """Google Calendar에 일정 생성"""
    service = get_calendar_service()
    if all_day:
        event = {
            "summary": title,
            "start": {"date": start_dt.strftime("%Y-%m-%d"), "timeZone": "Asia/Seoul"},
            "end": {"date": (start_dt + timedelta(days=1)).strftime("%Y-%m-%d"), "timeZone": "Asia/Seoul"},
        }
    else:
        event = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Seoul"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"},
        }
    result = service.events().insert(calendarId="primary", body=event).execute()
    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📅 캘린더 봇입니다!\n\n"
        "자연어로 일정을 입력하면 Google Calendar에 자동 등록됩니다.\n\n"
        "예시:\n"
        "• 내일 오후 3시 치과 예약\n"
        "• 4월 15일 팀 미팅\n"
        "• 모레 오전 10시 30분 면접\n"
        "• 다음주 월요일 저녁 7시 저녁 약속\n\n"
        "명령어:\n"
        "/today - 오늘 일정 확인\n"
        "/week - 이번 주 일정 확인"
    )
    await update.message.reply_text(msg)


async def today_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_calendar_service()
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "+09:00"
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat() + "+09:00"
    events_result = service.events().list(
        calendarId="primary", timeMin=start_of_day, timeMax=end_of_day,
        singleEvents=True, orderBy="startTime",
    ).execute()
    events = events_result.get("items", [])
    if not events:
        await update.message.reply_text("📅 오늘 등록된 일정이 없습니다.")
        return
    msg = f"📅 오늘의 일정 ({now.strftime('%m월 %d일')})\n━━━━━━━━━━━━━━━\n"
    for event in events:
        s = event["start"].get("dateTime", event["start"].get("date"))
        if "T" in s:
            t = datetime.fromisoformat(s)
            msg += f"• {t.strftime('%H:%M')} {event['summary']}\n"
        else:
            msg += f"• 종일 {event['summary']}\n"
    await update.message.reply_text(msg)


async def week_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = get_calendar_service()
    now = datetime.now()
    start_of_week = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "+09:00"
    end_of_week = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59).isoformat() + "+09:00"
    events_result = service.events().list(
        calendarId="primary", timeMin=start_of_week, timeMax=end_of_week,
        singleEvents=True, orderBy="startTime",
    ).execute()
    events = events_result.get("items", [])
    if not events:
        await update.message.reply_text("📅 이번 주 등록된 일정이 없습니다.")
        return
    msg = "📅 이번 주 일정\n━━━━━━━━━━━━━━━\n"
    current_date = ""
    for event in events:
        s = event["start"].get("dateTime", event["start"].get("date"))
        if "T" in s:
            t = datetime.fromisoformat(s)
            date_str = t.strftime("%m/%d (%a)")
            time_str = t.strftime("%H:%M")
        else:
            t = datetime.fromisoformat(s)
            date_str = t.strftime("%m/%d (%a)")
            time_str = "종일"
        if date_str != current_date:
            current_date = date_str
            msg += f"\n📌 {date_str}\n"
        msg += f"  • {time_str} {event['summary']}\n"
    await update.message.reply_text(msg)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return
    try:
        start_dt, end_dt, all_day = parse_datetime(text)
        title = extract_title(text)
        create_calendar_event(title, start_dt, end_dt, all_day)
        if all_day:
            time_str = start_dt.strftime("%Y년 %m월 %d일 (종일)")
        else:
            time_str = start_dt.strftime("%Y년 %m월 %d일 %H:%M")
        msg = f"✅ 일정이 등록되었습니다!\n\n📌 {title}\n🕐 {time_str}\n"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "❌ 일정 등록에 실패했습니다.\n다시 시도해주세요.\n\n예시: 내일 오후 3시 치과 예약"
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today_schedule))
    app.add_handler(CommandHandler("week", week_schedule))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 간단한 health check 서버 (Fly.io가 머신을 살려두도록)
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, format, *args):
            pass

    def run_health():
        HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), HealthHandler).serve_forever()

    threading.Thread(target=run_health, daemon=True).start()
    logger.info("Starting polling mode with health check")
    app.run_polling()


if __name__ == "__main__":
    main()
