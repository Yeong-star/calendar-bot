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
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")

SPREADSHEET_ID = "1HbmgFMC5QJku_igPVFjc5KinjcBMR4TwCNCnSFGEWWY"

# 카테고리 자동 분류 키워드
CATEGORY_KEYWORDS = {
    "카페": ["커피", "카페", "스타벅스", "투썸", "이디야", "메가", "컴포즈", "빽다방", "아메리카노", "라떼", "음료"],
    "음식": ["밥", "식사", "점심", "저녁", "아침", "배달", "치킨", "피자", "햄버거", "김밥", "떡볶이", "라면",
            "맥도날드", "버거킹", "도시락", "국밥", "냉면", "초밥", "회", "고기", "삼겹살", "족발", "보쌈",
            "중국집", "짜장", "짬뽕", "분식", "야식", "간식", "빵", "케이크", "디저트"],
    "교통": ["택시", "버스", "지하철", "기차", "KTX", "주유", "기름", "주차", "톨게이트", "교통"],
    "쇼핑": ["옷", "신발", "가방", "쇼핑", "의류", "악세사리", "화장품", "다이소"],
    "구독": ["넷플릭스", "유튜브", "스포티파이", "구독", "멜론", "왓챠", "디즈니", "클로드", "GPT", "앱"],
    "생활": ["마트", "편의점", "세탁", "이발", "미용", "약국", "병원", "세제", "휴지", "생필품"],
    "문화": ["영화", "공연", "전시", "책", "게임", "PC방", "노래방", "볼링"],
    "술": ["술", "맥주", "소주", "와인", "호프", "바", "포장마차", "이자카야"],
}


def get_creds():
    """Google API 인증 정보 반환"""
    env_token = os.environ.get("GOOGLE_TOKEN_JSON")
    if env_token:
        token_data = json.loads(env_token)
    else:
        with open(TOKEN_FILE) as f:
            token_data = json.load(f)
    creds = Credentials.from_authorized_user_info(token_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if not env_token:
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
    return creds


def get_calendar_service():
    return build("calendar", "v3", credentials=get_creds())


def get_sheets_service():
    return build("sheets", "v4", credentials=get_creds())


# ========== 가계부 기능 ==========

def classify_category(text):
    """키워드 기반 카테고리 자동 분류"""
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_lower:
                return category
    return "기타"


def is_expense_message(text):
    """금액이 포함된 지출 메시지인지 판별"""
    # 숫자가 포함되어 있고, 날짜/시간 패턴이 아닌 경우
    has_amount = bool(re.search(r"\d{3,}", text))
    has_date = bool(re.search(r"(\d{1,2})월\s*(\d{1,2})일|(\d{4})[-./](\d{1,2})[-./]|오늘|내일|모레|요일|\d{1,2}시", text))
    return has_amount and not has_date


def parse_expense(text):
    """지출 메시지에서 항목명과 금액 추출"""
    # "커피 4500" or "4500 커피" 패턴
    m = re.search(r"^(.+?)\s+(\d{1,3}(?:,?\d{3})*)\s*원?\s*$", text)
    if m:
        item = m.group(1).strip()
        amount = int(m.group(2).replace(",", ""))
        return item, amount

    m = re.search(r"^(\d{1,3}(?:,?\d{3})*)\s*원?\s+(.+)$", text)
    if m:
        amount = int(m.group(1).replace(",", ""))
        item = m.group(2).strip()
        return item, amount

    # 텍스트 내 금액 추출
    m = re.search(r"(\d{1,3}(?:,?\d{3})*)\s*원?", text)
    if m:
        amount = int(m.group(1).replace(",", ""))
        item = re.sub(r"\d{1,3}(?:,?\d{3})*\s*원?", "", text).strip()
        return item if item else "지출", amount

    return None, None


def add_expense_to_sheet(item, category, amount):
    """Google Sheets에 지출 기록 추가"""
    service = get_sheets_service()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="내역!A:E",
        valueInputOption="USER_ENTERED",
        body={"values": [[now, item, category, amount, ""]]},
    ).execute()


def get_monthly_summary(target_month=None):
    """특정 달 지출 요약 (기본: 이번 달)"""
    if not SPREADSHEET_ID:
        return None
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="내역!A:E",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return None

    if target_month is None:
        target_month = datetime.now().strftime("%Y-%m")
    category_totals = {}
    total = 0

    for row in rows[1:]:
        if len(row) >= 4 and row[0].startswith(target_month):
            cat = row[2]
            amt = int(row[3])
            category_totals[cat] = category_totals.get(cat, 0) + amt
            total += amt

    return category_totals, total


# ========== 일정 기능 (기존) ==========

def parse_datetime(text):
    now = datetime.now()
    year, month, day = now.year, now.month, now.day
    hour, minute, all_day = None, 0, False

    if "모레" in text:
        target = now + timedelta(days=2)
        year, month, day = target.year, target.month, target.day
    elif "내일" in text:
        target = now + timedelta(days=1)
        year, month, day = target.year, target.month, target.day

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
        month, day = int(m.group(1)), int(m.group(2))
        if month < now.month or (month == now.month and day < now.day):
            year += 1

    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))

    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m and not re.search(r"\d{4}[-./]", text):
        month, day = int(m.group(1)), int(m.group(2))

    pm = "오후" in text or "저녁" in text or "밤" in text
    am = "오전" in text or "아침" in text

    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})시\s*(\d{1,2})분", text)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
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
        return datetime(year, month, day), None, True
    else:
        start_dt = datetime(year, month, day, hour, minute)
        return start_dt, start_dt + timedelta(hours=1), False


def extract_title(text):
    title = text.strip()
    patterns = [
        r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", r"\d{1,2}월\s*\d{1,2}일", r"\d{1,2}/\d{1,2}",
        r"오전|오후|아침|저녁|밤", r"\d{1,2}시\s*\d{1,2}분", r"\d{1,2}시",
        r"\d{1,2}:\d{2}", r"오늘|내일|모레", r"다음\s*주",
        r"월요일|화요일|수요일|목요일|금요일|토요일|일요일", r"\d{1,2}분",
    ]
    for p in patterns:
        title = re.sub(p, "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title if title else "일정"


def create_calendar_event(title, start_dt, end_dt, all_day):
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
    return service.events().insert(calendarId="primary", body=event).execute()


CHAT_ID_FILE = os.path.join(BASE_DIR, "chat_id.txt")
OWNER_CHAT_ID = None


def save_chat_id(chat_id):
    global OWNER_CHAT_ID
    OWNER_CHAT_ID = chat_id
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except Exception:
        pass


def load_chat_id():
    global OWNER_CHAT_ID
    try:
        with open(CHAT_ID_FILE) as f:
            OWNER_CHAT_ID = int(f.read().strip())
    except Exception:
        pass


load_chat_id()


# ========== 핸들러 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_chat_id(update.message.chat_id)
    msg = (
        "📅💰 캘린더 & 가계부 봇\n"
        "━━━━━━━━━━━━━━━\n\n"
        "📅 일정 등록 (날짜/시간 포함)\n"
        "• 내일 오후 3시 치과 예약\n"
        "• 4월 15일 팀 미팅\n\n"
        "💰 지출 기록 (항목 + 금액)\n"
        "• 커피 4500\n"
        "• 택시 15000\n"
        "• 치킨 22000\n\n"
        "명령어:\n"
        "/today - 오늘 일정\n"
        "/week - 이번 주 일정\n"
        "/summary - 이번 달 지출 요약\n"
        "/recent - 최근 지출 5건"
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
            date_str, time_str = t.strftime("%m/%d (%a)"), t.strftime("%H:%M")
        else:
            t = datetime.fromisoformat(s)
            date_str, time_str = t.strftime("%m/%d (%a)"), "종일"
        if date_str != current_date:
            current_date = date_str
            msg += f"\n📌 {date_str}\n"
        msg += f"  • {time_str} {event['summary']}\n"
    await update.message.reply_text(msg)


async def monthly_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = get_monthly_summary()
    if not result:
        await update.message.reply_text("💰 이번 달 지출 내역이 없습니다.")
        return
    category_totals, total = result
    now = datetime.now()
    msg = f"💰 {now.strftime('%m월')} 지출 요약\n━━━━━━━━━━━━━━━\n"
    for cat, amt in sorted(category_totals.items(), key=lambda x: -x[1]):
        msg += f"• {cat}: {amt:,}원\n"
    msg += f"\n━━━━━━━━━━━━━━━\n💳 총 지출: {total:,}원"
    await update.message.reply_text(msg)


async def recent_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SPREADSHEET_ID:
        await update.message.reply_text("💰 지출 내역이 없습니다.")
        return
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="내역!A:E",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        await update.message.reply_text("💰 지출 내역이 없습니다.")
        return
    msg = "💰 최근 지출 5건\n━━━━━━━━━━━━━━━\n"
    for row in rows[-5:]:
        if len(row) >= 4:
            date = row[0].split(" ")[0]
            msg += f"• {date} | {row[1]} ({row[2]}) {int(row[3]):,}원\n"
    await update.message.reply_text(msg)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return
    save_chat_id(update.message.chat_id)

    # 지출인지 일정인지 판별
    if is_expense_message(text):
        try:
            item, amount = parse_expense(text)
            if item and amount:
                category = classify_category(item)
                add_expense_to_sheet(item, category, amount)
                msg = (
                    f"💰 지출이 기록되었습니다!\n\n"
                    f"📝 {item}\n"
                    f"📂 {category}\n"
                    f"💵 {amount:,}원"
                )
                await update.message.reply_text(msg)
                return
        except Exception as e:
            logger.error(f"Expense error: {e}")

    # 일정으로 처리
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
            "❌ 처리에 실패했습니다.\n\n"
            "💰 지출: 커피 4500\n"
            "📅 일정: 내일 오후 3시 치과"
        )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today_schedule))
    app.add_handler(CommandHandler("week", week_schedule))
    app.add_handler(CommandHandler("summary", monthly_summary))
    app.add_handler(CommandHandler("recent", recent_expenses))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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
    logger.info("Starting bot with calendar + expense tracking")
    # 매월 1일 오전 9시(KST)에 지난달 지출 요약 자동 전송
    async def send_monthly_report(context: ContextTypes.DEFAULT_TYPE):
        if not OWNER_CHAT_ID:
            return
        now = datetime.now()
        # 지난달 계산
        if now.month == 1:
            last_month = f"{now.year - 1}-12"
            month_name = "12월"
        else:
            last_month = f"{now.year}-{now.month - 1:02d}"
            month_name = f"{now.month - 1}월"

        result = get_monthly_summary(target_month=last_month)
        if not result or result[1] == 0:
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"📊 {month_name} 지출 요약\n━━━━━━━━━━━━━━━\n지난달 지출 내역이 없습니다."
            )
            return
        category_totals, total = result
        msg = f"📊 {month_name} 지출 요약\n━━━━━━━━━━━━━━━\n"
        for cat, amt in sorted(category_totals.items(), key=lambda x: -x[1]):
            msg += f"• {cat}: {amt:,}원\n"
        msg += f"\n━━━━━━━━━━━━━━━\n💳 총 지출: {total:,}원"
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=msg)

    from datetime import time as dt_time
    import pytz
    kst = pytz.timezone("Asia/Seoul")
    # 매월 1일 오전 9시 체크 (run_daily로 매일 체크, 1일만 실행)
    async def check_monthly_report(context: ContextTypes.DEFAULT_TYPE):
        if datetime.now().day == 1:
            await send_monthly_report(context)

    app.job_queue.run_daily(
        check_monthly_report,
        time=dt_time(hour=9, minute=0, tzinfo=kst),
    )

    import signal
    app.run_polling(drop_pending_updates=True, stop_signals=())


if __name__ == "__main__":
    main()
