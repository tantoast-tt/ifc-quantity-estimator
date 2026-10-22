"""물량표 × 단가표 → 공종별 견적."""
from pathlib import Path
import pandas as pd
from extract import run as extract_run, PYEONG

DEFAULT_PRICES = Path(__file__).parent / "data" / "unit_prices.csv"

# 산출기준 → 물량 계산 함수 (summary, spaces 데이터프레임을 받는다)
BASIS = {
    "벽체 면적":        lambda s, sp: _sum(s, "벽체", "면적 합계(㎡)"),
    "슬래브 면적":      lambda s, sp: _sum(s, "바닥/지붕 슬래브", "면적 합계(㎡)"),
    "구조 부재 개수":   lambda s, sp: _sum(s, "구조 부재(서까래 등)", "개수"),
    "보 개수":          lambda s, sp: _sum(s, "보", "개수"),
    "창호 면적":        lambda s, sp: _sum(s, "창호", "면적 합계(㎡)"),
    "문 개수":          lambda s, sp: _sum(s, "문", "개수"),
    "실 순면적":        lambda s, sp: float(_floor_area(sp)),
}


def _floor_area(spaces: pd.DataFrame) -> float:
    """연면적 집계 대상 실의 순면적 합 (지붕 등 제외)."""
    sp = spaces[spaces["연면적 포함"]] if "연면적 포함" in spaces else spaces
    return float(sp["순면적(㎡)"].fillna(0).sum())


def _sum(summary: pd.DataFrame, category: str, col: str) -> float:
    row = summary.loc[summary["분류"] == category, col]
    return float(row.iloc[0]) if len(row) else 0.0


def load_prices(path=DEFAULT_PRICES) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["단가(원)"] = pd.to_numeric(df["단가(원)"], errors="coerce").fillna(0)
    return df


def estimate(summary: pd.DataFrame, spaces: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """단가표 각 행에 물량을 붙이고 금액을 계산한다."""
    rows = []
    for _, p in prices.iterrows():
        fn = BASIS.get(p["산출기준"])
        qty = fn(summary, spaces) if fn else 0.0
        rows.append({
            "공종": p["공종"], "항목": p["항목"], "산출기준": p["산출기준"],
            "수량": round(qty, 2), "단위": p["단위"],
            "단가(원)": int(p["단가(원)"]),
            "금액(원)": int(round(qty * p["단가(원)"])),
            "비고": p.get("비고", ""),
        })
    return pd.DataFrame(rows)


def summarize_by_trade(est: pd.DataFrame, spaces: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """공종별 소계와 총액·평당가."""
    by = est.groupby("공종", sort=False)["금액(원)"].sum().reset_index()
    total = int(by["금액(원)"].sum())
    by["비중(%)"] = (by["금액(원)"] / total * 100).round(1) if total else 0
    area = _floor_area(spaces)
    pyeong = area / PYEONG
    kpi = {
        "총액(원)": total,
        "연면적(㎡)": round(area, 2),
        "연면적(평)": round(pyeong, 2),
        "㎡당(원)": int(round(total / area)) if area else 0,
        "평당(원)": int(round(total / pyeong)) if pyeong else 0,
    }
    return by, kpi


def to_excel(path, summary, spaces, est, by_trade, kpi, panels: pd.DataFrame | None = None):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([kpi]).T.rename(columns={0: "값"}).to_excel(xw, sheet_name="요약")
        by_trade.to_excel(xw, sheet_name="공종별", index=False)
        est.to_excel(xw, sheet_name="견적내역", index=False)
        summary.to_excel(xw, sheet_name="부재물량", index=False)
        spaces.to_excel(xw, sheet_name="실별면적", index=False)
        if panels is not None and len(panels):
            panels.to_excel(xw, sheet_name="패널일람", index=False)


if __name__ == "__main__":
    import sys
    ifc = sys.argv[1] if len(sys.argv) > 1 else "samples/FZK-Haus.ifc"
    schema, el, summ, sp = extract_run(ifc)
    prices = load_prices()
    est = estimate(summ, sp, prices)
    by, kpi = summarize_by_trade(est, sp)
    Path("output").mkdir(exist_ok=True)
    est.to_csv("output/estimate.csv", index=False, encoding="utf-8-sig")
    to_excel("output/estimate.xlsx", summ, sp, est, by, kpi)
    pd.options.display.float_format = "{:,.2f}".format
    print("[견적 내역]\n", est.drop(columns=["비고"]).to_string(index=False))
    print("\n[공종별]\n", by.to_string(index=False))
    print("\n" + "\n".join(f"{k}: {v:,}" for k, v in kpi.items()))
