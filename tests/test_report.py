from autoinvest import report

ROWS = [
    {"time": "2026-10-07 16:10:00", "actual": "2000", "baseline": "2010", "claude_stocks_daily": ""},
    {"time": "2026-10-08 16:10:00", "actual": "1900", "baseline": "1990", "claude_stocks_daily": "2000"},
    {"time": "2026-10-09 16:10:00", "actual": "2100", "baseline": "2020", "claude_stocks_daily": "1999"},
]


def test_series_skips_days_before_a_strategy_started():
    data = report.series(ROWS)
    assert data["actual"] == [("2026-10-07", 2000.0), ("2026-10-08", 1900.0), ("2026-10-09", 2100.0)]
    assert data["claude_stocks_daily"][0] == ("2026-10-08", 2000.0)


def test_summary_measures_from_the_budget():
    rows = report.summary(report.series(ROWS))
    assert [r["key"] for r in rows] == ["actual", "baseline", "claude_stocks_daily"]
    actual = rows[0]
    assert round(actual["total"], 4) == 0.05
    assert round(actual["max_dd"], 4) == 0.05          # 2000 -> 1900
    assert round(actual["day"], 4) == round(2100 / 1900 - 1, 4)
    assert actual["since"] == "2026-10-07"


def test_markdown_bolds_my_account():
    text = report.markdown(report.summary(report.series(ROWS)))
    assert "| 1 | **我的账户** | $2,100.00 | +5.00% |" in text
    assert "Claude 每日选股" in text


def test_svg_has_a_line_per_strategy_and_a_legend():
    out = report.svg(report.series(ROWS))
    assert out.startswith("<svg") and out.endswith("</svg>")
    assert out.count("<polyline") == 3
    assert "我的账户" in out and "其他策略" in out
    assert report.svg({}) == ""


def test_cli_writes_the_chart(tmp_path, capsys):
    csv_path = tmp_path / "strategies.csv"
    csv_path.write_text("time,actual,baseline\n2026-10-08 16:10:00,2000,2000\n2026-10-09 16:10:00,2010,1995\n",
                        encoding="utf-8")
    assert report.main(["--csv", str(csv_path), "--svg", str(tmp_path / "out" / "c.svg")]) == 0
    assert "我的账户" in capsys.readouterr().out
    assert (tmp_path / "out" / "c.svg").read_text(encoding="utf-8").startswith("<svg")


def test_default_csv_prefers_real_history(tmp_path):
    (tmp_path / "logs" / "real").mkdir(parents=True)
    (tmp_path / "logs" / "strategies.csv").write_text("time,actual\n", encoding="utf-8")
    assert report.default_csv(tmp_path) == tmp_path / "logs" / "strategies.csv"
    (tmp_path / "logs" / "real" / "strategies.csv").write_text("time,actual\n", encoding="utf-8")
    assert report.default_csv(tmp_path) == tmp_path / "logs" / "real" / "strategies.csv"
