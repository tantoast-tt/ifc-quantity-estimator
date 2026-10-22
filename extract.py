"""IFC 파일에서 부재별 물량과 실별 면적을 추출한다.

물량 값의 출처는 3단계로 폴백한다.
  1. BaseQuantities  — IFC 표준 수량 집합 (ArchiCAD 등)
  2. 기타 수량 Pset  — 예: Revit의 PSet_Revit_Dimensions
  3. 형상 계산        — 삼각 메시에서 체적·경계상자를 직접 계산
어느 단계에서 값을 얻었는지 '출처' 열에 기록한다.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import ifcopenshell
import ifcopenshell.geom
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

# 층명·실명 한글 매핑 (샘플 기준, 없으면 원문 유지)
STOREY_NAME_KO = {
    "Erdgeschoss": "1층", "Obergeschoss": "2층", "Dachgeschoss": "2층(다락)",
    "Level 1": "1층", "Level 2": "2층", "Roof": "지붕층", "T/FDN": "기초",
}
SPACE_NAME_KO = {
    "Schlafzimmer": "침실", "Bad": "욕실", "Buero": "서재", "Büro": "서재",
    "Wohnen": "거실", "Flur": "복도", "Küche": "주방", "Kueche": "주방", "Galerie": "다락/갤러리",
    "Living Room": "거실", "Kitchen": "주방", "Bathroom": "욕실", "Bedroom": "침실",
    "Hallway": "복도", "Foyer": "현관", "Stair": "계단실", "Utility": "다용도실",
    "Room": "실", "Roof": "지붕",
}

PYEONG = 3.305785  # 1평 = 3.305785㎡

# 수량 Pset에서 찾을 키 (대소문자 무시)
_AREA_KEYS = ("NetSideArea", "NetArea", "Area", "GrossArea", "GrossSideArea", "NetFloorArea")
_VOL_KEYS = ("NetVolume", "GrossVolume", "Volume")

_GEOM_SETTINGS = ifcopenshell.geom.settings()
_GEOM_SETTINGS.set("use-world-coords", True)


def _space_name_ko(raw: str) -> str:
    if raw in SPACE_NAME_KO:
        return SPACE_NAME_KO[raw]
    # "Bathroom 1" → "욕실 1"
    m = re.match(r"^(.*?)(\s+\d+)?$", raw)
    base, num = m.group(1), m.group(2) or ""
    return SPACE_NAME_KO.get(base, base) + num


def _storey(e):
    """소속 층 이름. 공간(IfcSpace)은 집합 관계(Decomposes)로 층에 묶인다."""
    c = ue.get_container(e)
    if c is None:
        agg = ue.get_aggregate(e)
        c = agg if agg is not None and agg.is_a("IfcBuildingStorey") else None
    if c is None:
        return ""
    return STOREY_NAME_KO.get(c.Name, c.Name)


def _material_name(e):
    m = ue.get_material(e, should_skip_usage=True)
    if m is None:
        return ""
    if m.is_a("IfcMaterialLayerSet"):
        return " / ".join(L.Material.Name for L in m.MaterialLayers if L.Material)
    if m.is_a("IfcMaterial"):
        return m.Name or ""
    return m.is_a()


def _pick(d: dict, keys) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _shape_arrays(e):
    try:
        sh = ifcopenshell.geom.create_shape(_GEOM_SETTINGS, e)
    except Exception:
        return None
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    fc = np.array(sh.geometry.faces, dtype=int).reshape(-1, 3)
    return (v, fc) if len(v) and len(fc) else None


def _meshes(e) -> list:
    """부재의 삼각 메시 목록. 자체 형상이 없으면 하위 부재(계단 → 계단판 등)의 메시."""
    m = _shape_arrays(e)
    if m is not None:
        return [m]
    return [m for p in ue.get_decomposition(e) if (m := _shape_arrays(p)) is not None]


def _signed_volume(v, fc) -> float:
    a, b, c = v[fc[:, 0]], v[fc[:, 1]], v[fc[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def _geom_dims(e) -> dict:
    """형상에서 체적과 경계상자 치수를 계산한다.

    체적은 메시 부호 체적(발산 정리)으로 구한다. 메시가 닫혀 있지 않으면(난간 등)
    값이 틀어지므로, 부품별로 |체적| > 경계상자 체적이면 신뢰 불가로 보고 제외한다.
    """
    parts = _meshes(e)
    if not parts:
        return {}
    volume, skipped = 0.0, 0
    for v, fc in parts:
        ext = v.max(axis=0) - v.min(axis=0)
        vol = abs(_signed_volume(v, fc))
        if vol <= float(np.prod(ext)) * 1.05:
            volume += vol
        else:
            skipped += 1
    allv = np.vstack([v for v, _ in parts])
    ext = np.sort(allv.max(axis=0) - allv.min(axis=0))  # 작은 순
    return {"volume": volume, "area_bbox": float(ext[2] * ext[1]),
            "length": float(ext[2]), "height": float(allv[:, 2].max() - allv[:, 2].min()),
            "width": float(ext[0]), "unreliable": skipped > 0}


def _dims(e) -> dict:
    """길이·폭·높이·면적·체적과 출처."""
    out = {"length": None, "width": None, "height": None, "area": None, "volume": None, "source": ""}
    # 1) BaseQuantities
    bq = ue.get_psets(e, qtos_only=True).get("BaseQuantities", {})
    if bq:
        out.update(length=_pick(bq, ("Length",)), width=_pick(bq, ("Width",)), height=_pick(bq, ("Height",)),
                   area=_pick(bq, _AREA_KEYS), volume=_pick(bq, _VOL_KEYS))
        if out["area"] or out["volume"]:
            out["source"] = "BaseQuantities"
            return out
    # 2) 다른 수량 Pset (이름에 Dimension/Quantit 포함, 또는 Area/Volume 키 보유)
    for name, ps in ue.get_psets(e).items():
        if not isinstance(ps, dict):
            continue
        area, vol = _pick(ps, _AREA_KEYS), _pick(ps, _VOL_KEYS)
        if area or vol:
            out.update(area=area, volume=vol, length=out["length"] or _pick(ps, ("Length",)),
                       width=out["width"] or _pick(ps, ("Width", "Thickness")),
                       height=out["height"] or _pick(ps, ("Height", "Unbounded Height")))
            out["source"] = name
            # 면적이 빠져 있으면 형상 경계상자로 보충 (벽·슬래브 등 판형 부재)
            if out["area"] is None:
                g = _geom_dims(e)
                if g:
                    out["height"] = out["height"] or g["height"]
                    out["length"] = out["length"] or g["length"]
                    out["width"] = out["width"] or g["width"]
                    if out["area"] is None and out["length"] and out["height"]:
                        out["area"] = out["length"] * out["height"]
                    out["source"] = f"{name} + 형상 보충"
            return out
    # 3) 형상 계산
    g = _geom_dims(e)
    if g:
        is_opening = e.is_a("IfcWindow") or e.is_a("IfcDoor")
        out.update(volume=g["volume"], area=g["area_bbox"] if is_opening else None,
                   length=g["length"], height=g["height"], width=g["width"],
                   source="형상 계산(일부 제외)" if g["unreliable"] else "형상 계산")
    return out


def extract_elements(f) -> pd.DataFrame:
    """부재 1개 = 1행."""
    rows = []
    for ifc_type, ko in ELEMENT_TYPES.items():
        for e in f.by_type(ifc_type):
            d = _dims(e)
            rows.append({
                "분류": ko, "IFC타입": e.is_a(), "이름": e.Name or "", "층": _storey(e),
                "재료": _material_name(e),
                "길이(m)": d["length"], "폭/두께(m)": d["width"], "높이(m)": d["height"],
                "면적(㎡)": d["area"], "체적(㎥)": d["volume"], "출처": d["source"],
                "GlobalId": e.GlobalId,
            })
    df = pd.DataFrame(rows)
    for c in ["길이(m)", "폭/두께(m)", "높이(m)", "면적(㎡)", "체적(㎥)"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").round(3)
    return df


def summarize_elements(df: pd.DataFrame) -> pd.DataFrame:
    """분류별 개수·면적·체적 합계와 출처."""
    g = df.groupby("분류", sort=False).agg(
        개수=("GlobalId", "count"),
        면적합계=("면적(㎡)", "sum"),
        체적합계=("체적(㎥)", "sum"),
        출처=("출처", lambda s: " / ".join(sorted(set(x for x in s if x)))),
    ).reset_index()
    g["면적합계"] = g["면적합계"].round(2)
    g["체적합계"] = g["체적합계"].round(3)
    return g.rename(columns={"면적합계": "면적 합계(㎡)", "체적합계": "체적 합계(㎥)"})


def extract_spaces(f) -> pd.DataFrame:
    rows = []
    for s in f.by_type("IfcSpace"):
        raw = s.LongName or s.Name or ""
        bq = ue.get_psets(s, qtos_only=True).get("BaseQuantities", {})
        area = _pick(bq, ("NetFloorArea", "GrossFloorArea"))
        height = _pick(bq, ("ClearHeight", "Height"))
        vol = _pick(bq, ("NetVolume", "GrossVolume"))
        src = "BaseQuantities" if area else ""
        if not area:
            # 면적이 있는 Pset 중 높이·체적까지 갖춘 것을 우선
            best = None
            for name, ps in ue.get_psets(s).items():
                if not isinstance(ps, dict):
                    continue
                a = _pick(ps, ("Area", "NetFloorArea", "GSA BIM Area"))
                if not a:
                    continue
                h = _pick(ps, ("Unbounded Height", "Height", "ClearHeight"))
                vv = _pick(ps, ("Volume",))
                score = (h is not None) + (vv is not None)
                if best is None or score > best[0]:
                    best = (score, name, a, h, vv)
            if best:
                _, src, area, height, vol = best
        storey = _storey(s)
        name_ko = _space_name_ko(raw)
        counted = not (storey == "지붕층" or name_ko in ("지붕",))
        rows.append({"실번호": s.Name or "", "실명": name_ko, "층": storey,
                     "순면적(㎡)": area, "천장고(m)": height, "체적(㎥)": vol, "출처": src,
                     "연면적 포함": counted})
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
    inc = sp[sp["연면적 포함"]]
    print(f"\n연면적(순): {inc['순면적(㎡)'].sum():.2f}㎡ = {inc['평'].sum():.2f}평  (집계 제외 {len(sp) - len(inc)}실)")
