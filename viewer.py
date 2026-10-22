"""IFC 형상 → plotly 3D 메시."""
import numpy as np
import pandas as pd
import ifcopenshell
import ifcopenshell.geom
import plotly.graph_objects as go

# 분류별 색 (extract.ELEMENT_TYPES의 한글명 기준)
COLORS = {
    "벽체": "#c9b79c", "바닥/지붕 슬래브": "#8d99ae", "지붕": "#9a6b4f",
    "보": "#6d597a", "기둥": "#6d597a", "구조 부재(서까래 등)": "#b5651d",
    "창호": "#7ec8e3", "문": "#e07a5f", "계단": "#81b29a", "마감재": "#f2cc8f",
}
OPACITY = {"창호": 0.45}


def build_meshes(f, elements: pd.DataFrame):
    """부재별 삼각 메시를 분류 단위로 모아 plotly 트레이스를 만든다."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)   # 전역 좌표로 바로 받기
    id2row = elements.set_index("GlobalId")
    buckets = {}   # 분류 → dict(x,y,z,i,j,k,text)

    it = ifcopenshell.geom.iterator(settings, f)
    if not it.initialize():
        return []
    while True:
        shape = it.get()
        gid = shape.guid
        if gid in id2row.index:
            row = id2row.loc[gid]
            cat = row["분류"]
            g = shape.geometry
            v = np.array(g.verts, dtype=float).reshape(-1, 3)
            fc = np.array(g.faces, dtype=int).reshape(-1, 3)
            b = buckets.setdefault(cat, {"x": [], "y": [], "z": [], "i": [], "j": [], "k": [], "text": []})
            off = len(b["x"])
            b["x"] += v[:, 0].tolist(); b["y"] += v[:, 1].tolist(); b["z"] += v[:, 2].tolist()
            b["i"] += (fc[:, 0] + off).tolist(); b["j"] += (fc[:, 1] + off).tolist(); b["k"] += (fc[:, 2] + off).tolist()
            area, vol = row["면적(㎡)"], row["체적(㎥)"]
            a_txt = f"{area:.2f}㎡" if pd.notna(area) else "–"
            v_txt = f"{vol:.3f}㎥" if pd.notna(vol) else "–"
            label = f"{cat} · {row['이름']}<br>층 {row['층']}<br>면적 {a_txt} · 체적 {v_txt}<br>출처 {row['출처'] or '–'}"
            b["text"] += [label] * len(v)
        if not it.next():
            break

    traces = []
    for cat, b in buckets.items():
        traces.append(go.Mesh3d(
            x=b["x"], y=b["y"], z=b["z"], i=b["i"], j=b["j"], k=b["k"],
            name=cat, color=COLORS.get(cat, "#999999"), opacity=OPACITY.get(cat, 1.0),
            text=b["text"], hoverinfo="text", flatshading=True, showlegend=True,
            lighting=dict(ambient=0.55, diffuse=0.8, specular=0.05),
        ))
    return traces


def figure(f, elements: pd.DataFrame, height=560) -> go.Figure:
    traces = build_meshes(f, elements)
    fig = go.Figure(data=traces)
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        scene=dict(aspectmode="data", xaxis_visible=False, yaxis_visible=False, zaxis_visible=False,
                   camera=dict(eye=dict(x=1.6, y=-1.6, z=1.0))),
        legend=dict(orientation="h", y=0.02, x=0.5, xanchor="center", itemclick="toggle"),
    )
    return fig
