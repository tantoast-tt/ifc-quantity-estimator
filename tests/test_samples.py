"""두 샘플의 핵심 수치를 고정해 회귀를 막는다. 실행: pytest -q"""
import math
from pathlib import Path

import ifcopenshell
import pytest

from extract import extract_elements, summarize_elements, extract_spaces
from estimate import load_prices, estimate, summarize_by_trade
from panels import wall_panels, panel_summary

ROOT = Path(__file__).resolve().parent.parent
FZK = ROOT / "samples" / "FZK-Haus.ifc"
DUPLEX = ROOT / "samples" / "Duplex.ifc"


@pytest.fixture(scope="module")
def fzk():
    f = ifcopenshell.open(str(FZK))
    el = extract_elements(f)
    return f, el, summarize_elements(el), extract_spaces(f)


@pytest.fixture(scope="module")
def duplex():
    f = ifcopenshell.open(str(DUPLEX))
    el = extract_elements(f)
    return f, el, summarize_elements(el), extract_spaces(f)


def _row(summ, cat):
    return summ.set_index("분류").loc[cat]


# ── FZK-Haus (ArchiCAD, IFC4) ─────────────────────────────
def test_fzk_counts(fzk):
    f, el, summ, sp = fzk
    assert f.schema == "IFC4"
    assert len(el) == 80 and len(sp) == 7
    assert int(_row(summ, "벽체")["개수"]) == 13
    assert int(_row(summ, "창호")["개수"]) == 11


def test_fzk_quantities_from_basequantities(fzk):
    _, _, summ, _ = fzk
    assert math.isclose(_row(summ, "벽체")["면적 합계(㎡)"], 190.46, abs_tol=0.01)
    assert math.isclose(_row(summ, "바닥/지붕 슬래브")["체적 합계(㎥)"], 76.991, abs_tol=0.001)
    assert _row(summ, "벽체")["출처"] == "BaseQuantities"


def test_fzk_floor_area_and_storeys(fzk):
    _, _, _, sp = fzk
    assert math.isclose(sp["순면적(㎡)"].sum(), 173.36, abs_tol=0.01)
    assert set(sp["층"]) == {"1층", "2층(다락)"}
    assert sp["연면적 포함"].all()


def test_fzk_estimate_total(fzk):
    _, _, summ, sp = fzk
    est = estimate(summ, sp, load_prices())
    by, kpi = summarize_by_trade(est, sp)
    assert kpi["총액(원)"] == 151_871_600
    assert kpi["평당(원)"] == 2_896_025
    assert math.isclose(by["비중(%)"].sum(), 100.0, abs_tol=0.2)


def test_fzk_panels_split(fzk):
    f, el, _, _ = fzk
    p12 = panel_summary(wall_panels(f, el, max_len=12.0))
    p6 = panel_summary(wall_panels(f, el, max_len=6.0))
    assert p12["벽체 수"] == 13 and p12["운송 초과 벽체"] == 0
    assert p6["패널 수(분할 후)"] == 22 and p6["운송 초과 벽체"] == 9
    assert p6["개구부 합계"] == 16  # 창 11 + 문 5


# ── Duplex (Revit, IFC2x3) ────────────────────────────────
def test_duplex_fallback_sources(duplex):
    f, el, summ, _ = duplex
    assert f.schema == "IFC2X3"
    assert len(el) == 144
    assert _row(summ, "벽체")["출처"].startswith("PSet_Revit_Dimensions")
    assert "형상 계산" in _row(summ, "문")["출처"]


def test_duplex_geometry_volume_sane(duplex):
    _, el, _, _ = duplex
    stairs = el[el["분류"] == "계단"]
    # 열린 메시(난간)를 제외한 뒤 계단 체적은 경계상자 체적(각 ~17㎥)보다 훨씬 작아야 한다
    assert 0 < stairs["체적(㎥)"].sum() < 3
    assert (stairs["출처"] == "형상 계산(일부 제외)").all()


def test_duplex_roof_space_excluded(duplex):
    _, _, _, sp = duplex
    assert len(sp) == 21
    assert (~sp["연면적 포함"]).sum() == 1
    inc = sp[sp["연면적 포함"]]
    assert math.isclose(inc["순면적(㎡)"].sum(), 276.34, abs_tol=0.01)


def test_duplex_estimate_total(duplex):
    _, _, summ, sp = duplex
    est = estimate(summ, sp, load_prices())
    _, kpi = summarize_by_trade(est, sp)
    assert kpi["총액(원)"] == 303_790_500
    assert math.isclose(kpi["연면적(㎡)"], 276.34, abs_tol=0.01)


def test_duplex_panels_derive_height(duplex):
    f, el, _, _ = duplex
    p = wall_panels(f, el)
    assert p["높이(m)"].notna().all()
    assert (p["비고"].str.contains("유도값")).any()
