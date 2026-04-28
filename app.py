import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image

try:
    import pytesseract

    TESSERACT_AVAILABLE = True
except Exception:
    TESSERACT_AVAILABLE = False


WEEKDAY_MAP = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


@dataclass
class ShiftRow:
    day_name: str
    date_value: Optional[date]
    start: str
    end: str
    break_minutes: int


def parse_time_to_datetime(base_date: date, time_text: str) -> datetime:
    cleaned = time_text.strip().lower().replace(".", "")
    fmts = ["%H:%M", "%H%M", "%I:%M%p", "%I%p", "%I:%M %p", "%I %p"]
    for fmt in fmts:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return datetime.combine(base_date, parsed.time())
        except ValueError:
            continue
    raise ValueError(f"Unsupported time format: {time_text}")


def day_name_from_date(d: date) -> str:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return names[d.weekday()]


def normalize_day_name(raw_day: str) -> str:
    key = raw_day.strip().lower()
    if key not in WEEKDAY_MAP:
        return raw_day.title()
    idx = WEEKDAY_MAP[key]
    return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][idx]


def extract_text_from_image(file_bytes: bytes) -> str:
    if not TESSERACT_AVAILABLE:
        return ""
    image = Image.open(io.BytesIO(file_bytes))
    return pytesseract.image_to_string(image)


def parse_timesheet_text(raw_text: str, week_start: date) -> List[ShiftRow]:
    rows: List[ShiftRow] = []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    pattern = re.compile(
        r"(?P<day>[A-Za-z]{3,9})\s+"
        r"(?P<start>\d{1,2}(?::?\d{2})?\s?(?:am|pm|AM|PM)?)\s*[-to]+\s*"
        r"(?P<end>\d{1,2}(?::?\d{2})?\s?(?:am|pm|AM|PM)?)"
        r"(?:\s+break[:\s]*(?P<break>\d{1,3}))?",
        re.IGNORECASE,
    )

    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        day_name = normalize_day_name(match.group("day"))
        target_idx = WEEKDAY_MAP.get(day_name.lower()[:3], None)
        row_date = None
        if target_idx is not None:
            row_date = week_start + timedelta(days=target_idx)
        break_minutes = int(match.group("break") or 0)
        rows.append(
            ShiftRow(
                day_name=day_name,
                date_value=row_date,
                start=match.group("start").strip(),
                end=match.group("end").strip(),
                break_minutes=break_minutes,
            )
        )

    return rows


def calculate_hours(
    rows: List[ShiftRow],
    regular_daily_limit: float,
    overtime_multiplier: float,
    weekend_multiplier: float,
    public_holiday_multiplier: float,
    holiday_dates: List[date],
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    output_rows = []
    totals = {
        "regular_hours": 0.0,
        "overtime_hours": 0.0,
        "weekend_hours": 0.0,
        "public_holiday_hours": 0.0,
        "weighted_total_hours": 0.0,
    }

    holiday_set = set(holiday_dates)

    for row in rows:
        if row.date_value is None:
            continue
        start_dt = parse_time_to_datetime(row.date_value, row.start)
        end_dt = parse_time_to_datetime(row.date_value, row.end)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        total_hours = (end_dt - start_dt).total_seconds() / 3600.0
        total_hours -= row.break_minutes / 60.0
        total_hours = max(total_hours, 0.0)

        is_weekend = row.date_value.weekday() >= 5
        is_holiday = row.date_value in holiday_set

        regular_hours = 0.0
        overtime_hours = 0.0
        weekend_hours = 0.0
        public_holiday_hours = 0.0
        weighted_hours = 0.0

        if is_holiday:
            public_holiday_hours = total_hours
            weighted_hours = total_hours * public_holiday_multiplier
        elif is_weekend:
            weekend_hours = total_hours
            weighted_hours = total_hours * weekend_multiplier
        else:
            regular_hours = min(total_hours, regular_daily_limit)
            overtime_hours = max(total_hours - regular_daily_limit, 0.0)
            weighted_hours = regular_hours + overtime_hours * overtime_multiplier

        totals["regular_hours"] += regular_hours
        totals["overtime_hours"] += overtime_hours
        totals["weekend_hours"] += weekend_hours
        totals["public_holiday_hours"] += public_holiday_hours
        totals["weighted_total_hours"] += weighted_hours

        output_rows.append(
            {
                "day": row.day_name,
                "date": row.date_value.isoformat(),
                "start": row.start,
                "end": row.end,
                "break_minutes": row.break_minutes,
                "raw_hours": round(total_hours, 2),
                "regular_hours": round(regular_hours, 2),
                "overtime_hours": round(overtime_hours, 2),
                "weekend_hours": round(weekend_hours, 2),
                "public_holiday_hours": round(public_holiday_hours, 2),
                "weighted_hours": round(weighted_hours, 2),
            }
        )

    totals = {k: round(v, 2) for k, v in totals.items()}
    return pd.DataFrame(output_rows), totals


def parse_holiday_dates(raw_holiday_text: str) -> List[date]:
    holidays = []
    for line in raw_holiday_text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        try:
            holidays.append(datetime.strptime(cleaned, "%Y-%m-%d").date())
        except ValueError:
            continue
    return holidays


def default_rows_from_week(week_start: date) -> List[ShiftRow]:
    rows = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        rows.append(
            ShiftRow(
                day_name=day_name_from_date(d),
                date_value=d,
                start="09:00",
                end="17:00",
                break_minutes=30,
            )
        )
    return rows


def render_manual_editor(rows: List[ShiftRow]) -> List[ShiftRow]:
    table_data = []
    for row in rows:
        table_data.append(
            {
                "day": row.day_name,
                "date": row.date_value.isoformat() if row.date_value else "",
                "start": row.start,
                "end": row.end,
                "break_minutes": row.break_minutes,
            }
        )

    edited_df = st.data_editor(
        pd.DataFrame(table_data),
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "day": st.column_config.TextColumn("Day"),
            "date": st.column_config.TextColumn("Date (YYYY-MM-DD)"),
            "start": st.column_config.TextColumn("Start"),
            "end": st.column_config.TextColumn("End"),
            "break_minutes": st.column_config.NumberColumn("Break (mins)", min_value=0, step=5),
        },
        key="timesheet_editor",
    )

    parsed_rows: List[ShiftRow] = []
    for _, row in edited_df.iterrows():
        row_date = None
        raw_date = str(row.get("date", "")).strip()
        if raw_date:
            try:
                row_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                row_date = None
        parsed_rows.append(
            ShiftRow(
                day_name=str(row.get("day", "")).strip() or (day_name_from_date(row_date) if row_date else ""),
                date_value=row_date,
                start=str(row.get("start", "")).strip(),
                end=str(row.get("end", "")).strip(),
                break_minutes=int(row.get("break_minutes", 0) or 0),
            )
        )
    return parsed_rows


def main() -> None:
    st.set_page_config(page_title="Timesheet Photo Extractor", layout="wide")
    st.title("Timesheet Photo Extractor + Rules Engine")
    st.caption("Upload or take a photo of your timesheet, extract shifts, and calculate hours by your pay rules.")

    with st.sidebar:
        st.subheader("Rules")
        regular_daily_limit = st.number_input("Regular daily hours", min_value=0.0, max_value=24.0, value=8.0, step=0.5)
        overtime_multiplier = st.number_input("Overtime multiplier", min_value=1.0, max_value=5.0, value=1.5, step=0.1)
        weekend_multiplier = st.number_input("Weekend multiplier", min_value=1.0, max_value=5.0, value=1.5, step=0.1)
        public_holiday_multiplier = st.number_input(
            "Public holiday multiplier", min_value=1.0, max_value=5.0, value=2.5, step=0.1
        )
        week_start = st.date_input("Week start date (Monday)", value=date.today() - timedelta(days=date.today().weekday()))
        holiday_text = st.text_area(
            "Public holidays (one YYYY-MM-DD per line)",
            value="",
            help="Example:\n2026-01-01\n2026-04-25",
        )

    st.subheader("1) Add your timesheet image")
    uploaded_file = st.file_uploader("Upload photo", type=["png", "jpg", "jpeg"])
    camera_file = st.camera_input("Or take photo")
    selected_file = camera_file or uploaded_file

    raw_ocr_text = ""
    parsed_rows: List[ShiftRow] = []

    c1, c2 = st.columns(2)
    with c1:
        if selected_file is not None:
            bytes_data = selected_file.read()
            st.image(bytes_data, caption="Timesheet image", use_container_width=True)
            if TESSERACT_AVAILABLE:
                if st.button("Extract data from image"):
                    raw_ocr_text = extract_text_from_image(bytes_data)
                    st.session_state["ocr_text"] = raw_ocr_text
            else:
                st.warning("OCR engine not installed yet. Install Tesseract on your system, then restart app.")

        if "ocr_text" in st.session_state:
            raw_ocr_text = st.session_state["ocr_text"]

    with c2:
        st.subheader("2) OCR text / paste text")
        raw_ocr_text = st.text_area(
            "Detected text",
            value=raw_ocr_text,
            height=250,
            help="You can correct OCR text manually. Expected line pattern example: Monday 9:00am - 5:00pm break 30",
        )

        if st.button("Parse text into shifts"):
            parsed_rows = parse_timesheet_text(raw_ocr_text, week_start)
            st.session_state["rows"] = parsed_rows

    if "rows" not in st.session_state:
        st.session_state["rows"] = default_rows_from_week(week_start)

    st.subheader("3) Review / edit extracted shifts")
    edited_rows = render_manual_editor(st.session_state["rows"])
    st.session_state["rows"] = edited_rows

    st.subheader("4) Calculate output hours")
    if st.button("Calculate"):
        holidays = parse_holiday_dates(holiday_text)
        result_df, totals = calculate_hours(
            rows=edited_rows,
            regular_daily_limit=regular_daily_limit,
            overtime_multiplier=overtime_multiplier,
            weekend_multiplier=weekend_multiplier,
            public_holiday_multiplier=public_holiday_multiplier,
            holiday_dates=holidays,
        )

        st.success("Calculation complete")
        st.dataframe(result_df, use_container_width=True)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Regular", totals["regular_hours"])
        m2.metric("Overtime", totals["overtime_hours"])
        m3.metric("Weekend", totals["weekend_hours"])
        m4.metric("Public Holiday", totals["public_holiday_hours"])
        m5.metric("Weighted Total", totals["weighted_total_hours"])

        csv_data = result_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", data=csv_data, file_name="timesheet_hours_output.csv", mime="text/csv")

    st.markdown(
        """
        ---
        **Tips**
        - OCR quality depends on image clarity and handwriting.
        - If OCR misses fields, paste/fix text in the OCR box and parse again.
        - You can fully edit shift rows before calculation.
        """
    )


if __name__ == "__main__":
    main()
