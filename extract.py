"""IFC 파일에서 부재별 물량과 실별 면적을 추출한다."""
from pathlib import Path
import pandas as pd
import ifcopenshell
import ifcopenshell.util.element as ue

# 추출 대상 부재와 한글명
ELEMENT_TYPES = {
    "IfcWall": "벽체",
    "IfcSlab": "바닥/지붕 슬래브",
    "IfcRoof": "지붕",
    "IfcBeam": "보",
    "IfcColumn": "기둥",
    "IfcMember": "구조 부재(서까래 등)",
    "IfcWindow": "창호",
    "IfcDoor": "문",
    "IfcStair": "계단",
    "IfcCovering": "마감재",
}

# 실 이름 한글 매핑 (샘플이 독일어라 임시 매핑, 없으면 원문 유지)
STOREY_NAME_KO = {"Erdgeschoss": "1층", "Dachgeschoss": "2층(다락)", "Obergeschoss": "2층"}
SPACE_NAME_KO = {
    "Schlafzimmer": "침실", "Bad": "욕실", "Buero": "서재", "Büro": "서재",
    "Wohnen": "거실", "Flur": "복도", "Küche": "주방", "Kueche": "주방",
    "Galerie": "다락/갤러리",
}

PYEONG = 3.305785  # 1평 = 3.305785㎡


def _qty(e, key, qset="BaseQuantities"):
    """BaseQuantities 값 조회. 없으면 None."""
    return ue.get_psets(e, qtos_only=True).get(qset, {}).get(key)


def _material_name(e):
    m = ue.get_material(e, should_skip_usage=True)
    if m is None:
        return ""
    if m.is_a("IfcMaterialLayerSet"):
        return " / ".join(L.Material.Name for L in m.MaterialLayers if L.Material)
    if m.is_a("IfcMaterial"):
        return m.Name or ""
    return m.is_a()


def _storey(e):
    """소속 층 이름. 공간(IfcSpace)은 집합 관계(Decomposes)로 층에 묶인다."""
    c = ue.get_container(e)
    if c is None:
        agg = ue.get_aggregate(e)
        c = agg if agg is not None and agg.is_a("IfcBuildingStorey") else None
    if c is None:
        return ""
    return STOREY_NAME_KO.get(c.Name, c.Name)


def extract_elements(f) -> pd.DataFrame:
    """부재 1개 = 1행. 면적·체적은 BaseQuantities 기준."""
    rows = []
    for ifc_type, ko in ELEMENT_TYPES.items():
        for e in f.by_type(ifc_type):
            # 면적: 벽은 측면, 슬래브는 평면, 창/문은 개구부, 그 외 없음
            area = (
                _qty(e, "NetSideArea") or _qty(e, "NetArea")
                or _qty(e, "Area") or _qty(e, "GrossArea")
            )
            rows.append({
                "분류": ko,
                "IFC타입": e.is_a(),
                "이름": e.Name or "",
                "층": _storey(e),
                "재료": _material_name(e),
                "길이(m)": _qty(e, "Length"),
                "폭/두께(m)": _qty(e, "Width"),
                "높이(m)": _qty(e, "Height"),
                "면적(㎡)": area,
                "체적(㎥)": _qty(e, "NetVolume") or _qty(e, "GrossVolume"),
                "GlobalId": e.GlobalId,
            })
    df = pd.DataFrame(rows)
    for c in ["길이(m)", "폭/두께(m)", "높이(m)", "면적(㎡)", "체적(㎥)"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").round(3)
    return df


def summarize_elements(df: pd.DataFrame) -> pd.DataFrame:
    """분류별 개수·면적·체적 합계."""
    g = df.groupby("분류", sort=False).agg(
        개수=("GlobalId", "count"),
        면적합계=("면적(㎡)", "sum"),
        체적합계=("체적(㎥)", "sum"),
    ).reset_index()
    g["면적합계"] = g["면적합계"].round(2)
    g["체적합계"] = g["체적합계"].round(3)
    return g.rename(columns={"면적합계": "면적 합계(㎡)", "체적합계": "체적 합계(㎥)"})


def extract_spaces(f) -> pd.DataFrame:
    rows = []
    for s in f.by_type("IfcSpace"):
        raw = s.LongName or s.Name or ""
        rows.append({
            "실번호": s.Name or "",
            "실명": SPACE_NAME_KO.get(raw, raw),
            "층": _storey(s),
            "순면적(㎡)": _qty(s, "NetFloorArea"),
            "천장고(m)": _qty(s, "ClearHeight") or _qty(s, "Height"),
            "체적(㎥)": _qty(s, "NetVolume"),
        })
    df = pd.DataFrame(rows)
    for c in ["순면적(㎡)", "천장고(m)", "체적(㎥)"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").round(2)
    df["평"] = (df["순면적(㎡)"] / PYEONG).round(2)
    return df.sort_values(["층", "실번호"]).reset_index(drop=True)


def run(ifc_path, out_dir="output"):
    f = ifcopenshell.open(str(ifc_path))
    el = extract_elements(f)
    summ = summarize_elements(el)
    sp = extract_spaces(f)
    out = Path(out_dir); out.mkdir(exist_ok=True)
    el.to_csv(out / "elements.csv", index=False, encoding="utf-8-sig")
    summ.to_csv(out / "summary.csv", index=False, encoding="utf-8-sig")
    sp.to_csv(out / "spaces.csv", index=False, encoding="utf-8-sig")
    return f.schema, el, summ, sp


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "samples/FZK-Haus.ifc"
    schema, el, summ, sp = run(path)
    print("schema:", schema, "| 부재", len(el), "개 | 실", len(sp), "개")
    print("\n[부재 요약]\n", summ.to_string(index=False))
    print("\n[실별 면적]\n", sp.to_string(index=False))
    print(f"\n연면적(순): {sp['순면적(㎡)'].sum():.2f}㎡ = {sp['평'].sum():.2f}평")
