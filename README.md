# Timesheet Rules Engine

Streamlit app to calculate timesheet pay using OCR or manual entry, with day-off, weekend, shift allowance, overtime, and public holiday rules.

## Features

- Choose input mode:
  - `Photo extract` (upload image and OCR)
  - `Manual entry` (edit shifts directly in table)
- Correct OCR text before parsing shifts
- Fully edit parsed shifts before calculation
- Supports one or two day-off selections
- Handles shift allowance buckets:
  - `+50%` allowance hours
  - `+100%` allowance hours
- Handles overtime buckets:
  - `Time and half (1.5x)`
  - `Double time (2x)`
- Public holiday multiplier support (default `2.5x`)
- CSV export of calculated results

## Rule Summary (Current)

- Minimum unpaid break deduction per shift (default `30` minutes)
- Public holiday hours paid by public holiday multiplier
- Standard day logic:
  - First `8h` normal
  - Next `2h` at `1.5x`
  - Remaining at `2x`
- Saturday:
  - First `2h` at `1.5x`
  - Remaining at `2x`
- Sunday:
  - All hours at `2x`
- Day off worked:
  - First `2h` at `1.5x`
  - Remaining at `2x`
- Special case when both days off are weekdays:
  - Saturday/Sunday first `8h` become normal hours
  - Saturday also gets shift allowance `+50%` for those hours
  - Sunday also gets shift allowance `+100%` for those hours
- Weekly regular-hour limit (default `40h`) with overflow converted to:
  - First overtime tier at `1.5x` (default `2h`)
  - Remaining overflow at `2x`

## Quick Start

1. Create and activate a virtual environment (recommended)
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run app:

```bash
streamlit run app.py
```

## OCR Requirement

The app uses `pytesseract`, which requires Tesseract OCR installed on your machine.

- Windows install guide: <https://github.com/UB-Mannheim/tesseract/wiki>
- If needed, set `pytesseract.pytesseract.tesseract_cmd` in `app.py` to your local install path.

## Expected OCR Line Format

Best results use one shift per line:

```text
Monday 9:00am - 5:00pm break 30
Tuesday 08:30 - 17:15 break 45
Saturday 10am - 2pm
```

