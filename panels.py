"""벽체 패널 일람표 — 모듈러·패널라이징 공법을 위한 벽체 단위 정리.

벽 1장 = 공장 제작 패널 1장으로 보고, 길이·높이·개구부·순면적을 정리한다.
운송 한계 길이를 넘는 벽은 분할 수를 제안한다.
"""
import math
import pandas as pd
import ifcopenshell.util.element as ue

DEFAULT_MAX_LEN = 12.0  # m, 트럭 적재 한계 가정값


def _is_external(w) -> str:
    v = ue.get_psets(w).get("Pset_WallCommon", {}).get("IsExternal")
    if v is True:
        return "외벽"
    if v is False:
        return "내벽"
    n = (w.Name or "").lower()
    if "ext" in n or "exterior" in n or "party" in n:
        return "외벽(이름 추정)"
    if "int" in n or "interior" in n:
        return "내벽(이름 추정)"
    return "미상"


def _openings(w, el_by_gid: dict):
    """벽에 뚫린 개구부의 (창 수, 문 수, 개구부 면적 합)."""
    win = door = 0
    area = 0.0
    for rel in getattr(w, "HasOpenings", []) or []:
        op = rel.RelatedOpeningElement
        for fr in getattr(op, "HasFillings", []) or []:
            e = fr.RelatedBuildingElement
            if e.is_a("IfcWindow"):
                win += 1
            elif e.is_a("IfcDoor"):
                door += 1
            else:
                continue
            a = el_by_gid.get(e.GlobalId)
            if a is not None and a == a:  # not NaN
                area += float(a)
    return win, door, area


def wall_panels(f, elements: pd.DataFrame, max_len: float = DEFAULT_MAX_LEN) -> pd.DataFrame:
    el = elements.set_index("GlobalId")
    area_by_gid = el["면적(㎡)"].to_dict()
    rows = []
    for w in f.by_type("IfcWall"):
        if w.GlobalId not in el.index:
            continue
        r = el.loc[w.GlobalId]
        L, H, T, A, V = r["길이(m)"], r["높이(m)"], r["폭/두께(m)"], r["면적(㎡)"], r["체적(㎥)"]
        # 수량 Pset에 높이·두께가 없으면 면적·체적에서 유도
        derived = []
        if not H == H and A == A and L == L and L > 0:
            H, _ = round(A / L, 3), derived.append("높이")
        if not T == T and V == V and A == A and A > 0:
            T, _ = round(V / A, 3), derived.append("두께")
        win, door, op_area = _openings(w, area_by_gid)
        gross = A if A == A else (L * H if (L == L and H == H) else float("nan"))
        net = gross - op_area if gross == gross else float("nan")
        n_split = int(math.ceil(L / max_len)) if (L == L and max_len > 0) else 1
        rows.append({
            "벽체": w.Name or w.GlobalId, "층": r["층"], "구분": _is_external(w),
            "길이(m)": L, "높이(m)": H, "두께(m)": T,
            "창": win, "문": door, "개구부 면적(㎡)": round(op_area, 2),
            "총면적(㎡)": round(gross, 2) if gross == gross else None,
            "순패널면적(㎡)": round(net, 2) if net == net else None,
            "분할 수": n_split,
            "분할 길이(m)": round(L / n_split, 2) if (L == L and n_split) else None,
            "운송 초과": n_split > 1,
            "비고": "·".join(derived) + " 유도값" if derived else "",
            "재료": r["재료"],
        })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["층", "구분", "길이(m)"], ascending=[True, True, False]).reset_index(drop=True)
    return df


def panel_summary(df: pd.DataFrame) -> dict:
    if not len(df):
        return {}
    ext = df["구분"].str.startswith("외벽")
    return {
        "벽체 수": int(len(df)),
        "패널 수(분할 후)": int(df["분할 수"].sum()),
        "운송 초과 벽체": int(df["운송 초과"].sum()),
        "최장 벽체(m)": float(df["길이(m)"].max()),
        "외벽 순패널면적(㎡)": round(float(df.loc[ext, "순패널면적(㎡)"].fillna(0).sum()), 2),
        "내벽 순패널면적(㎡)": round(float(df.loc[~ext, "순패널면적(㎡)"].fillna(0).sum()), 2),
        "개구부 합계": int(df["창"].sum() + df["문"].sum()),
    }


if __name__ == "__main__":
    import sys, ifcopenshell
    from extract import extract_elements
    path = sys.argv[1] if len(sys.argv) > 1 else "samples/FZK-Haus.ifc"
    f = ifcopenshell.open(path)
    el = extract_elements(f)
    df = wall_panels(f, el, max_len=float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_LEN)
    pd.options.display.width = 200
    print(df.drop(columns=["재료"]).to_string(index=False))
    print(panel_summary(df))
