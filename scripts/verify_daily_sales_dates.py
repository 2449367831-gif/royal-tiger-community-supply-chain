"""Business-date selection boundaries; never substitute arbitrarily old data."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.query_daily_sales_web import default_forecast_day

today = date(2026, 11, 11)
yesterday = today - timedelta(days=1)
assert default_forecast_day(today, {today, yesterday}) == (today, 'today')
assert default_forecast_day(today, {yesterday}) == (yesterday, 'yesterday')
assert default_forecast_day(today, set()) == (today, 'missing')
assert default_forecast_day(today, {today - timedelta(days=2)}) == (today, 'missing')
assert default_forecast_day(date(2027, 1, 1), {date(2026, 12, 31)}) == (date(2026, 12, 31), 'yesterday')
print('Forecast date selection passed: today first, yesterday fallback, no older fallback, year boundary.')
