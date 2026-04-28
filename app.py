"""
Weekly Timesheet Calculator
===========================
Computes hour breakdowns across multiplier tiers for each day of the week.

Usage:
    python timesheet_calculator.py
"""

from dataclasses import dataclass, field
from typing import Optional
import math

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    days_off: list[str] = field(default_factory=lambda: ['Saturday', 'Sunday'])
    break_minutes: int = 30               # unpaid break deducted per shift
    weekly_regular_limit: float = 40.0   # hours before weekly OT kicks in
    weekly_ot1_limit: float = 2.0        # first OT tier cap (×1.5) before ×2
    public_holiday_multiplier: float = 2.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DayEntry:
    start_time: str = '07:30'   # HH:MM
    end_time: str = '17:00'     # HH:MM
    is_public_holiday: bool = False
    did_work: bool = False


@dataclass
class Segment:
    hours: float
    multiplier: float
    label: str
    shift_allowance: Optional[float] = None   # 0.5 = +50%, 1.0 = +100%


@dataclass
class DayResult:
    day_name: str
    is_public_holiday: bool
    raw_hours: float
    worked_hours: float
    breakdown: list[Segment]


@dataclass
class WeekTotals:
    normal: float = 0.0
    ot1_5: float = 0.0
    ot2: float = 0.0
    ph: float = 0.0
    shift_allowance_50: float = 0.0
    shift_allowance_100: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def time_to_hours(time_str: str) -> float:
    h, m = map(int, time_str.split(':'))
    return h + m / 60.0


def fmt_hours(h: float) -> str:
    hrs = int(abs(h))
    mins = round((abs(h) - hrs) * 60)
    return f"{hrs}h {mins:02d}m"


def get_day_type(day_name: str, config: Config) -> str:
    """
    Returns one of:
      'weekday' | 'saturday' | 'sunday' |
      'day_off_weekday' | 'day_off_sat' | 'day_off_sun'
    """
    is_day_off = day_name in config.days_off
    if day_name == 'Saturday':
        return 'day_off_sat' if is_day_off else 'saturday'
    if day_name == 'Sunday':
        return 'day_off_sun' if is_day_off else 'sunday'
    return 'day_off_weekday' if is_day_off else 'weekday'


def both_days_off_are_weekdays(config: Config) -> bool:
    return all(d not in ('Saturday', 'Sunday') for d in config.days_off)


def split_tiers(hours: float, tiers: list[dict]) -> list[Segment]:
    """
    tiers: list of dicts with keys:
      limit       – cumulative hour limit (float or math.inf)
      multiplier  – pay multiplier
      label       – display label
      shift_allowance – optional float (0.5 or 1.0)
    """
    result = []
    remaining = hours
    used = 0.0

    for tier in tiers:
        if remaining <= 0:
            break
        limit = tier['limit']
        available = remaining if limit == math.inf else min(remaining, limit - used)
        if available <= 0:
            continue
        taken = min(remaining, available)
        result.append(Segment(
            hours=taken,
            multiplier=tier['multiplier'],
            label=tier['label'],
            shift_allowance=tier.get('shift_allowance'),
        ))
        remaining -= taken
        used += taken

    return result


# ---------------------------------------------------------------------------
# Per-day breakdown
# ---------------------------------------------------------------------------

def calc_day_breakdown(day_name: str, raw_hours: float,
                       is_public_holiday: bool, config: Config) -> list[Segment]:
    break_hours = config.break_minutes / 60.0
    worked = max(0.0, raw_hours - break_hours)

    if worked == 0:
        return []

    # Public holiday
    if is_public_holiday:
        m = config.public_holiday_multiplier
        return [Segment(hours=worked, multiplier=m, label=f'PH ×{m}')]

    day_type = get_day_type(day_name, config)
    special = both_days_off_are_weekdays(config)

    # Day off – weekday
    if day_type == 'day_off_weekday':
        return split_tiers(worked, [
            {'limit': 2,        'multiplier': 1.5, 'label': '×1.5 (day off)'},
            {'limit': math.inf, 'multiplier': 2.0, 'label': '×2 (day off OT)'},
        ])

    # Saturday
    if day_type == 'saturday':
        if special:
            # First 2h ×1 +50% shift, next 6h ×1 +100% shift, remainder ×2
            return split_tiers(worked, [
                {'limit': 2,        'multiplier': 1, 'label': '×1 normal (+50% shift)',  'shift_allowance': 0.5},
                {'limit': 8,        'multiplier': 1, 'label': '×1 normal (+100% shift)', 'shift_allowance': 1.0},
                {'limit': math.inf, 'multiplier': 2, 'label': '×2'},
            ])
        return split_tiers(worked, [
            {'limit': 2,        'multiplier': 1.5, 'label': '×1.5'},
            {'limit': math.inf, 'multiplier': 2.0, 'label': '×2'},
        ])

    # Sunday
    if day_type == 'sunday':
        if special:
            # First 8h ×1 +100% shift, remainder ×2
            return split_tiers(worked, [
                {'limit': 8,        'multiplier': 1, 'label': '×1 normal (+100% shift)', 'shift_allowance': 1.0},
                {'limit': math.inf, 'multiplier': 2, 'label': '×2'},
            ])
        return [Segment(hours=worked, multiplier=2.0, label='×2')]

    # Day off on Saturday
    if day_type == 'day_off_sat':
        return split_tiers(worked, [
            {'limit': 2,        'multiplier': 1.5, 'label': '×1.5 (day off)'},
            {'limit': math.inf, 'multiplier': 2.0, 'label': '×2 (day off OT)'},
        ])

    # Day off on Sunday
    if day_type == 'day_off_sun':
        return [Segment(hours=worked, multiplier=2.0, label='×2 (day off)')]

    # Standard weekday
    return split_tiers(worked, [
        {'limit': 8,        'multiplier': 1.0, 'label': '×1 normal'},
        {'limit': 10,       'multiplier': 1.5, 'label': '×1.5 OT'},
        {'limit': math.inf, 'multiplier': 2.0, 'label': '×2 OT'},
    ])


# ---------------------------------------------------------------------------
# Weekly overflow conversion
# ---------------------------------------------------------------------------

def apply_weekly_overflow(day_results: list[DayResult], config: Config) -> list[DayResult]:
    """Convert weekday normal hours that exceed the weekly regular limit to OT."""
    import copy
    results = copy.deepcopy(day_results)

    # Count total normal (×1, no shift allowance) weekday hours
    total_normal = sum(
        seg.hours
        for r in results
        if not r.is_public_holiday and get_day_type(r.day_name, config) == 'weekday'
        for seg in r.breakdown
        if seg.multiplier == 1 and not seg.shift_allowance
    )

    if total_normal <= config.weekly_regular_limit:
        return results

    overflow = total_normal - config.weekly_regular_limit
    weekly_ot1_used = 0.0

    # Walk backwards through weekdays, converting normal → OT
    for r in reversed(results):
        if overflow <= 0:
            break
        if r.is_public_holiday or get_day_type(r.day_name, config) != 'weekday':
            continue

        new_breakdown = []
        for seg in reversed(r.breakdown):
            if seg.multiplier == 1 and not seg.shift_allowance and overflow > 0:
                convert = min(seg.hours, overflow)
                overflow -= convert

                ot1_available = max(0.0, config.weekly_ot1_limit - weekly_ot1_used)
                ot1 = min(convert, ot1_available)
                ot2 = convert - ot1
                weekly_ot1_used += ot1

                remaining_normal = seg.hours - convert
                if remaining_normal > 0:
                    new_breakdown.insert(0, Segment(remaining_normal, 1.0, seg.label))
                if ot1 > 0:
                    new_breakdown.insert(0 if remaining_normal <= 0 else 1,
                                         Segment(ot1, 1.5, '×1.5 weekly OT'))
                if ot2 > 0:
                    new_breakdown.append(Segment(ot2, 2.0, '×2 weekly OT'))
            else:
                new_breakdown.insert(0, seg)

        r.breakdown = new_breakdown

    return results


# ---------------------------------------------------------------------------
# Full week calculation
# ---------------------------------------------------------------------------

def calculate_week(week_data: dict[str, DayEntry], config: Config):
    """
    week_data: dict mapping day name → DayEntry

    Returns (day_results, totals)
    """
    day_results = []
    for day_name in DAYS:
        entry = week_data.get(day_name, DayEntry())
        if not entry.did_work or not entry.start_time or not entry.end_time:
            day_results.append(DayResult(
                day_name=day_name,
                is_public_holiday=entry.is_public_holiday,
                raw_hours=0, worked_hours=0, breakdown=[],
            ))
            continue

        raw_hours = time_to_hours(entry.end_time) - time_to_hours(entry.start_time)
        if raw_hours <= 0:
            day_results.append(DayResult(
                day_name=day_name,
                is_public_holiday=entry.is_public_holiday,
                raw_hours=0, worked_hours=0, breakdown=[],
            ))
            continue

        worked_hours = max(0.0, raw_hours - config.break_minutes / 60.0)
        breakdown = calc_day_breakdown(day_name, raw_hours, entry.is_public_holiday, config)
        day_results.append(DayResult(
            day_name=day_name,
            is_public_holiday=entry.is_public_holiday,
            raw_hours=raw_hours,
            worked_hours=worked_hours,
            breakdown=breakdown,
        ))

    adjusted = apply_weekly_overflow(day_results, config)

    totals = WeekTotals()
    for r in adjusted:
        for seg in r.breakdown:
            if r.is_public_holiday or seg.label.startswith('PH'):
                totals.ph += seg.hours
            elif seg.multiplier == 1:
                totals.normal += seg.hours
                if seg.shift_allowance == 0.5:
                    totals.shift_allowance_50 += seg.hours
                if seg.shift_allowance == 1.0:
                    totals.shift_allowance_100 += seg.hours
            elif seg.multiplier == 1.5:
                totals.ot1_5 += seg.hours
            elif seg.multiplier == 2:
                totals.ot2 += seg.hours

    return adjusted, totals


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

def print_results(adjusted: list[DayResult], totals: WeekTotals, config: Config):
    special = both_days_off_are_weekdays(config)
    print("\n" + "=" * 60)
    print("  WEEKLY TIMESHEET BREAKDOWN")
    print("=" * 60)
    print(f"  Days off : {', '.join(config.days_off)}")
    print(f"  Break    : {config.break_minutes} min")
    print(f"  Weekly limit: {config.weekly_regular_limit}h  |  OT1 cap: {config.weekly_ot1_limit}h")
    if special:
        print("  ⚠  Special case: both days off are weekdays")
    print("-" * 60)

    for r in adjusted:
        if not r.breakdown:
            print(f"  {r.day_name:<12}  —  (not worked)")
            continue
        tag = " [PH]" if r.is_public_holiday else ""
        print(f"\n  {r.day_name:<12}{tag}  raw: {fmt_hours(r.raw_hours)}  worked: {fmt_hours(r.worked_hours)}")
        for seg in r.breakdown:
            sa = f"  (+{int(seg.shift_allowance * 100)}% shift)" if seg.shift_allowance else ""
            print(f"    {fmt_hours(seg.hours):>10}  {seg.label}{sa}")

    print("\n" + "-" * 60)
    print("  WEEKLY TOTALS")
    print("-" * 60)
    rows = [
        ("Normal ×1",          totals.normal),
        ("Overtime ×1.5",      totals.ot1_5),
        ("Overtime ×2",        totals.ot2),
        ("Public Holiday",     totals.ph),
        ("Shift Allow. +50%",  totals.shift_allowance_50),
        ("Shift Allow. +100%", totals.shift_allowance_100),
    ]
    total_worked = sum(r.worked_hours for r in adjusted)
    for label, value in rows:
        if value > 0.001:
            print(f"  {label:<22}  {fmt_hours(value):>10}")
    print("-" * 60)
    print(f"  {'TOTAL WORKED':<22}  {fmt_hours(total_worked):>10}")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Example / demo
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    config = Config(
        days_off=['Saturday', 'Sunday'],
        break_minutes=30,
        weekly_regular_limit=40,
        weekly_ot1_limit=2,
        public_holiday_multiplier=2.0,
    )

    week_data = {
        'Monday':    DayEntry(start_time='07:30', end_time='17:00', did_work=True),
        'Tuesday':   DayEntry(start_time='07:30', end_time='17:00', did_work=True),
        'Wednesday': DayEntry(start_time='07:30', end_time='17:00', did_work=True),
        'Thursday':  DayEntry(start_time='07:30', end_time='17:00', did_work=True),
        'Friday':    DayEntry(start_time='07:30', end_time='17:00', did_work=True),
        'Saturday':  DayEntry(start_time='07:30', end_time='17:00', did_work=True),
        'Sunday':    DayEntry(start_time='07:30', end_time='13:00', did_work=True),
    }

    adjusted, totals = calculate_week(week_data, config)
    print_results(adjusted, totals, config)
