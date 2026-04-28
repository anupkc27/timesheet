import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:
    import pytesseract

    TESSERACT_AVAILABLE = True
except Exception:
    TESSERACT_AVAILABLE = False


try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    # Pillow < 9 compatibility.
    RESAMPLE_LANCZOS = Image.LANCZOS

TESSERACT_RUNTIME_AVAILABLE = False
if TESSERACT_AVAILABLE:
    try:
        _ = pytesseract.get_tesseract_version()
        TESSERACT_RUNTIME_AVAILABLE = True
    except Exception:
        TESSERACT_RUNTIME_AVAILABLE = False


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
    rostered_day_off: bool = False
    worked: bool = True


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


def extract_text_from_image(file_bytes: bytes, fast_mode: bool = True) -> str:
    if not TESSERACT_AVAILABLE:
        return ""
    image = Image.open(io.BytesIO(file_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")
    try:
        # OCR works better on high-contrast, sharpened images.
        if fast_mode:
            # Keep runtime low by limiting image size in fast mode.
            max_side = 1800
            scale = min(max_side / max(image.width, image.height), 1.0)
            if scale < 1.0:
                image = image.resize((int(image.width * scale), int(image.height * scale)), RESAMPLE_LANCZOS)
            enlarged = image
        else:
            enlarged = image.resize((image.width * 2, image.height * 2), RESAMPLE_LANCZOS)
        grayscale = ImageOps.grayscale(enlarged)
        sharpened = grayscale.filter(ImageFilter.SHARPEN)
        high_contrast = ImageEnhance.Contrast(sharpened).enhance(2.0)

        # Binary threshold version for faint text.
        thresholded = high_contrast.point(lambda p: 255 if p > 150 else 0)

        # Try slight deskew rotations for non-straight photos.
        angles = [0] if fast_mode else [-6, -3, 0, 3, 6]
        variants = []
        for base in [high_contrast, thresholded]:
            for angle in angles:
                rotated = base.rotate(angle, expand=True, fillcolor=255)
                variants.append(rotated)

        # Keep original as fallback.
        variants.append(image)
        if fast_mode:
            configs = ["--oem 3 --psm 6"]
        else:
            configs = [
                "--oem 3 --psm 6",
                "--oem 3 --psm 4",
                "--oem 3 --psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:-/ .",
            ]

        def score_text(text: str) -> int:
            if not text:
                return 0
            lower = text.lower()
            day_hits = sum(1 for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] if d in lower)
            time_hits = len(re.findall(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)?\b", lower))
            break_hits = len(re.findall(r"\bbreak\b", lower))
            noise_penalty = len(re.findall(r"[^A-Za-z0-9:\-\s/\n\.]", text))
            return day_hits * 8 + time_hits * 2 + break_hits - noise_penalty

        best_text = ""
        best_score = -1
        for v in variants:
            for config in configs:
                extracted = pytesseract.image_to_string(v, config=config)
                score = score_text(extracted)
                if score > best_score:
                    best_score = score
                    best_text = extracted

        cleaned_lines = []
        for line in best_text.splitlines():
            cleaned = re.sub(r"[^\w:\-\s/\.]", " ", line)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                cleaned_lines.append(cleaned)
        return "\n".join(cleaned_lines)
    except Exception:
        # Covers missing Tesseract binary/runtime (e.g., Streamlit Cloud without system package).
        return ""


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
                rostered_day_off=False,
                worked=True,
            )
        )

    return rows


def sync_day_names_from_dates(rows: List[ShiftRow]) -> List[ShiftRow]:
    synced: List[ShiftRow] = []
    for row in rows:
        resolved_day = day_name_from_date(row.date_value) if row.date_value else row.day_name
        synced.append(
            ShiftRow(
                day_name=resolved_day,
                date_value=row.date_value,
                start=row.start,
                end=row.end,
                break_minutes=row.break_minutes,
                rostered_day_off=row.rostered_day_off,
                worked=row.worked,
            )
        )
    return synced


def calculate_hours(
    rows: List[ShiftRow],
    public_holiday_multiplier: float,
    holiday_dates: List[date],
    day_off_weekdays: List[int],
    unpaid_break_minutes: int = 30,
    weekly_regular_limit: float = 40.0,
    weekly_overtime_tier1_limit: float = 2.0,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    output_rows = []
    totals = {
        "hours_1x": 0.0,
        "hours_1_5x": 0.0,
        "hours_2x": 0.0,
        "shift_allowance_50_hours": 0.0,
        "shift_allowance_100_hours": 0.0,
        "public_holiday_hours": 0.0,
        "weighted_total_hours": 0.0,
    }

    holiday_set = set(holiday_dates)
    day_off_set = set(day_off_weekdays)

    # Apply weekly overtime conversion on base 1x hours after the limit:
    # first tier at 1.5x, remaining at 2x.
    cumulative_base_hours = 0.0
    cumulative_weekly_excess = 0.0
    worked_days_by_date: Dict[date, int] = {}
    worked_day_counter = 0

    for row in sorted(rows, key=lambda r: r.date_value or date.min):
        if row.date_value is None:
            continue
        if not row.worked:
            continue
        start_dt = parse_time_to_datetime(row.date_value, row.start)
        end_dt = parse_time_to_datetime(row.date_value, row.end)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        total_hours = (end_dt - start_dt).total_seconds() / 3600.0
        # Always deduct at least the standard unpaid 30-minute break.
        effective_break_minutes = max(int(row.break_minutes or 0), unpaid_break_minutes)
        total_hours -= effective_break_minutes / 60.0
        total_hours = max(total_hours, 0.0)

        if total_hours > 0 and row.date_value not in worked_days_by_date:
            worked_day_counter += 1
            worked_days_by_date[row.date_value] = worked_day_counter
        worked_day_number = worked_days_by_date.get(row.date_value, 0)

        weekday_idx = row.date_value.weekday()
        is_holiday = row.date_value in holiday_set

        hours_1x = 0.0
        hours_1_5x = 0.0
        hours_2x = 0.0
        shift_allowance_50_hours = 0.0
        shift_allowance_100_hours = 0.0
        public_holiday_hours = 0.0
        weighted_hours = 0.0

        if is_holiday:
            public_holiday_hours = total_hours
            weighted_hours = total_hours * public_holiday_multiplier
        elif row.rostered_day_off and worked_day_number in (6, 7):
            # RDO worked on 6th/7th worked day: first 2h at 1.5x, remaining at 2x.
            hours_1_5x = min(total_hours, 2.0)
            hours_2x = max(total_hours - 2.0, 0.0)
        elif weekday_idx == 5:
            # Saturday: first 8h normal, with parallel shift allowance split:
            # first 2h at +50%, next 6h at +100%. Remaining hours are double time.
            weekend_normal_hours = min(total_hours, 8.0)
            hours_1x = weekend_normal_hours
            shift_allowance_50_hours = min(weekend_normal_hours, 2.0)
            shift_allowance_100_hours = min(max(weekend_normal_hours - 2.0, 0.0), 6.0)
            hours_2x = max(total_hours - weekend_normal_hours, 0.0)
        elif weekday_idx == 6:
            # Sunday: first 8h normal with +100% shift allowance. Remaining are double time.
            weekend_normal_hours = min(total_hours, 8.0)
            hours_1x = weekend_normal_hours
            shift_allowance_100_hours = weekend_normal_hours
            hours_2x = max(total_hours - weekend_normal_hours, 0.0)
        elif weekday_idx in day_off_set:
            # Day off worked: first 2 hours at 1.5x, remaining at 2x.
            hours_1_5x = min(total_hours, 2.0)
            hours_2x = max(total_hours - 2.0, 0.0)
        else:
            # Other days: first 8h at 1x, next 2h at 1.5x, then 2x.
            hours_1x = min(total_hours, 8.0)
            remaining_after_8 = max(total_hours - 8.0, 0.0)
            hours_1_5x = min(remaining_after_8, 2.0)
            hours_2x = max(remaining_after_8 - 2.0, 0.0)

        if not is_holiday and hours_1x > 0:
            prior_excess = max(cumulative_base_hours - weekly_regular_limit, 0.0)
            cumulative_base_hours += hours_1x
            new_excess_total = max(cumulative_base_hours - weekly_regular_limit, 0.0)
            converted_from_1x = max(new_excess_total - prior_excess, 0.0)
            converted_from_1x = min(converted_from_1x, hours_1x)
            if converted_from_1x > 0:
                hours_1x -= converted_from_1x
                remaining_tier1_capacity = max(weekly_overtime_tier1_limit - cumulative_weekly_excess, 0.0)
                tier1_allocation = min(converted_from_1x, remaining_tier1_capacity)
                tier2_allocation = max(converted_from_1x - tier1_allocation, 0.0)
                hours_1_5x += tier1_allocation
                hours_2x += tier2_allocation
                cumulative_weekly_excess += converted_from_1x

        weighted_hours = (
            hours_1x
            + hours_1_5x * 1.5
            + hours_2x * 2.0
            + shift_allowance_50_hours * 0.5
            + shift_allowance_100_hours * 1.0
            + public_holiday_hours * public_holiday_multiplier
        )

        totals["hours_1x"] += hours_1x
        totals["hours_1_5x"] += hours_1_5x
        totals["hours_2x"] += hours_2x
        totals["shift_allowance_50_hours"] += shift_allowance_50_hours
        totals["shift_allowance_100_hours"] += shift_allowance_100_hours
        totals["public_holiday_hours"] += public_holiday_hours
        totals["weighted_total_hours"] += weighted_hours

        output_rows.append(
            {
                "day": row.day_name,
                "date": row.date_value.isoformat(),
                "start": row.start,
                "end": row.end,
                "break_minutes": effective_break_minutes,
                "rostered_day_off": row.rostered_day_off,
                "worked_day_number": worked_day_number,
                "raw_hours": round(total_hours, 2),
                "hours_1x": round(hours_1x, 2),
                "hours_1_5x": round(hours_1_5x, 2),
                "hours_2x": round(hours_2x, 2),
                "shift_allowance_50_hours": round(shift_allowance_50_hours, 2),
                "shift_allowance_100_hours": round(shift_allowance_100_hours, 2),
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
                start="7:30 AM",
                end="17:00",
                break_minutes=30,
                rostered_day_off=False,
                worked=True,
            )
        )
    return rows


def render_manual_editor(rows: List[ShiftRow]) -> List[ShiftRow]:
    table_data = []
    for row in rows:
        table_data.append(
            {
                "day": row.day_name,
                "date": row.date_value,
                "start": row.start,
                "end": row.end,
                "break_minutes": row.break_minutes,
                "rostered_day_off": row.rostered_day_off,
                "worked": row.worked,
            }
        )

    edited_df = st.data_editor(
        pd.DataFrame(table_data),
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "day": st.column_config.TextColumn("Day", disabled=True, help="Auto-filled from Date"),
            "date": st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "start": st.column_config.TextColumn("Start"),
            "end": st.column_config.TextColumn("End"),
            "break_minutes": st.column_config.NumberColumn("Break (mins)", min_value=0, step=5),
            "rostered_day_off": st.column_config.CheckboxColumn("Rostered Day Off"),
            "worked": st.column_config.CheckboxColumn("Worked", help="Untick if you did not work this day"),
        },
        key="timesheet_editor",
    )

    parsed_rows: List[ShiftRow] = []
    for _, row in edited_df.iterrows():
        row_date = None
        raw_date = row.get("date", "")
        if raw_date:
            if isinstance(raw_date, datetime):
                row_date = raw_date.date()
            elif isinstance(raw_date, date):
                row_date = raw_date
            else:
                raw_date_text = str(raw_date).strip()
                try:
                    row_date = datetime.strptime(raw_date_text, "%Y-%m-%d").date()
                except ValueError:
                    row_date = None
        resolved_day_name = day_name_from_date(row_date) if row_date else str(row.get("day", "")).strip()
        parsed_rows.append(
            ShiftRow(
                day_name=resolved_day_name,
                date_value=row_date,
                start=str(row.get("start", "")).strip(),
                end=str(row.get("end", "")).strip(),
                break_minutes=int(row.get("break_minutes", 0) or 0),
                rostered_day_off=bool(row.get("rostered_day_off", False)),
                worked=bool(row.get("worked", True)),
            )
        )
    return parsed_rows


def main() -> None:
    st.set_page_config(page_title="Timesheet Photo Extractor", layout="wide")
    st.title("Timesheet Photo Extractor + Rules Engine")
    st.caption("Use manual entry or photo extraction, then adjust shifts and calculate hours by your pay rules.")

    with st.sidebar:
        st.subheader("Rules")
        st.caption(
            "Rules: weekday 8h@1x, next 2h@1.5x, then 2x; "
            "Saturday first 8h normal (+2h @50% allowance, +6h @100% allowance), then 2x; "
            "Sunday first 8h normal (+100% allowance), then 2x; "
            "RDO worked on 6th/7th day = first 2h@1.5x then 2x; PH 2.5x."
        )
        day_off_names = st.multiselect(
            "Day off(s) (up to 2)",
            options=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            default=[],
        )
        if len(day_off_names) > 2:
            st.warning("Please select at most 2 days off.")
            day_off_names = day_off_names[:2]
        unpaid_break_minutes = st.number_input("Unpaid break per shift (mins)", min_value=0, max_value=240, value=30, step=5)
        weekly_regular_limit = st.number_input("Weekly regular-hour limit", min_value=0.0, max_value=168.0, value=40.0, step=1.0)
        weekly_overtime_tier1_limit = st.number_input(
            "Weekly overtime @1.5x hours (after limit)", min_value=0.0, max_value=40.0, value=2.0, step=0.5
        )
        public_holiday_multiplier = st.number_input(
            "Public holiday multiplier", min_value=1.0, max_value=5.0, value=2.5, step=0.1
        )
        week_start = st.date_input("Week start date (Monday)", value=date.today() - timedelta(days=date.today().weekday()))
        holiday_text = st.text_area(
            "Public holidays (one YYYY-MM-DD per line)",
            value="",
            help="Example:\n2026-01-01\n2026-04-25",
        )

    st.subheader("1) Timesheet input")
    input_method = st.radio(
        "Choose input method",
        options=["Photo extract", "Manual entry"],
        horizontal=True,
    )

    raw_ocr_text = st.session_state.get("ocr_text", "")
    parsed_rows: List[ShiftRow] = []

    if input_method == "Photo extract":
        st.caption("Upload an image (you can use your device camera from the file picker) and extract shifts.")
        uploaded_file = st.file_uploader("Upload or take photo", type=["png", "jpg", "jpeg"])
        fast_ocr_mode = st.checkbox("Fast OCR mode (recommended)", value=True)

        c1, c2 = st.columns(2)
        with c1:
            if uploaded_file is not None:
                bytes_data = uploaded_file.read()
                st.image(bytes_data, caption="Timesheet image", use_container_width=True)
                if st.button("Extract data from image"):
                    with st.spinner("Extracting text..."):
                        raw_ocr_text = extract_text_from_image(bytes_data, fast_mode=fast_ocr_mode)
                    if raw_ocr_text.strip():
                        st.session_state["ocr_text"] = raw_ocr_text
                    else:
                        if not TESSERACT_RUNTIME_AVAILABLE:
                            st.warning(
                                "OCR is unavailable in this environment. Please paste times manually, "
                                "or install Tesseract OCR on the host system and restart."
                            )
                        else:
                            st.warning(
                                "OCR could not read clear timesheet text from this image. "
                                "Try a clearer/straighter photo, or turn off Fast OCR mode."
                            )

        with c2:
            st.subheader("2) OCR text / paste text")
            raw_ocr_text = st.text_area(
                "Detected text",
                value=raw_ocr_text,
                height=250,
                help="You can fix OCR errors here before parsing. Example: Monday 9:00am - 5:00pm break 30",
            )
            st.session_state["ocr_text"] = raw_ocr_text

            if st.button("Parse text into shifts"):
                parsed_rows = parse_timesheet_text(raw_ocr_text, week_start)
                if parsed_rows:
                    st.session_state["rows"] = parsed_rows
                    st.session_state["rows_week_start"] = week_start
                else:
                    st.warning(
                        "Could not detect valid shift lines from OCR text. "
                        "Please correct/paste lines like: Monday 9:00am - 5:00pm break 30, then parse again."
                    )
    else:
        st.caption("Enter or adjust shift rows directly in the table below.")

    if "rows" not in st.session_state:
        st.session_state["rows"] = default_rows_from_week(week_start)
        st.session_state["rows_week_start"] = week_start
    else:
        stored_week_start = st.session_state.get("rows_week_start")
        if stored_week_start != week_start:
            st.session_state["rows"] = default_rows_from_week(week_start)
            st.session_state["rows_week_start"] = week_start

    st.subheader("3) Review / edit shifts")
    st.caption("Week view: rows auto-load from selected week start date through Sunday. Untick Worked for days not worked.")
    if st.button("Reset shifts to selected week"):
        st.session_state["rows"] = default_rows_from_week(week_start)
        st.session_state["rows_week_start"] = week_start
        st.rerun()
    edited_rows = render_manual_editor(st.session_state["rows"])
    st.session_state["rows"] = edited_rows

    st.subheader("4) Calculate output hours")
    if st.button("Calculate"):
        selected_day_offs = [WEEKDAY_MAP[d.lower()] for d in day_off_names]
        holidays = parse_holiday_dates(holiday_text)
        result_df, totals = calculate_hours(
            rows=edited_rows,
            public_holiday_multiplier=public_holiday_multiplier,
            holiday_dates=holidays,
            day_off_weekdays=selected_day_offs,
            unpaid_break_minutes=int(unpaid_break_minutes),
            weekly_regular_limit=float(weekly_regular_limit),
            weekly_overtime_tier1_limit=float(weekly_overtime_tier1_limit),
        )

        st.success("Calculation complete")
        display_df = result_df.rename(
            columns={
                "hours_1x": "normal_hours",
                "hours_1_5x": "overtime_time_and_half_hours",
                "hours_2x": "overtime_double_time_hours",
                "shift_allowance_50_hours": "shift_allowance_50pct_hours",
                "shift_allowance_100_hours": "shift_allowance_100pct_hours",
            }
        )
        preferred_order = [
            "day",
            "date",
            "start",
            "end",
            "break_minutes",
            "worked",
            "rostered_day_off",
            "worked_day_number",
            "raw_hours",
            "normal_hours",
            "shift_allowance_100pct_hours",
            "shift_allowance_50pct_hours",
            "overtime_time_and_half_hours",
            "overtime_double_time_hours",
            "public_holiday_hours",
            "weighted_hours",
        ]
        display_cols = [c for c in preferred_order if c in display_df.columns]
        st.dataframe(display_df[display_cols], use_container_width=True)
        st.caption("Overtime buckets include remaining overtime hours across both weekday and weekend shifts.")

        m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
        m1.metric("Normal Hours", totals["hours_1x"])
        m2.metric("Shift Allowance 100%", totals["shift_allowance_100_hours"])
        m3.metric("Shift Allowance 50%", totals["shift_allowance_50_hours"])
        m4.metric("Overtime Time & Half", totals["hours_1_5x"])
        m5.metric("Overtime Double Time", totals["hours_2x"])
        m6.metric("Public Holiday Hours", totals["public_holiday_hours"])
        m7.metric("Weighted Total", totals["weighted_total_hours"])

        csv_data = display_df.to_csv(index=False).encode("utf-8")
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
