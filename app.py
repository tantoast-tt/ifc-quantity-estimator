"""IFC Quantity Estimator — Streamlit UI"""
import io
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import ifcopenshell

from extract import run as extract_run
from estimate import load_prices, estimate, summarize_by_trade, to_excel
from viewer import figure as viewer_figure
from panels import wall_panels, panel_summary, DEFAULT_MAX_LEN

SAMPLES = {
    "FZK-Haus — ArchiCAD, IFC4, 2층 단독주택": Path(__file__).parent / "samples" / "FZK-Haus.ifc",
    "Duplex — Revit, IFC2x3, 2세대 듀플렉스": Path(__file__).parent / "samples" / "Duplex.ifc",
}

st.set_page_config(page_title="IFC Quantity Estimator", layout="wide")
st.title("IFC Quantity Estimator")
st.caption("BIM(IFC) 모델 → 부재 물량 · 실별 면적 → 단가표 적용 → 개략 견적")

# ── 1. 입력 ───────────────────────────────────────────────
with st.sidebar:
    st.header("입력")
    up = st.file_uploader("IFC 파일 업로드", type=["ifc"])
    use_sample = st.checkbox("샘플 사용", value=up is None)
    sample_key = st.radio("샘플", list(SAMPLES), disabled=not use_sample, label_visibility="collapsed")
    st.divider()
    st.markdown(
        "**사용 순서**\n1. IFC 업로드 또는 샘플 선택\n2. 물량·면적 확인\n"
        "3. 단가표 수정 → 견적 자동 재계산\n4. 엑셀 다운로드"
    )


@st.cache_data(show_spinner="IFC 파싱 중…")
def parse(file_bytes: bytes, name: str):
    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    schema, el, summ, sp = extract_run(path, out_dir=tempfile.mkdtemp())
    return schema, el, summ, sp, path


@st.cache_data(show_spinner="패널 일람표 계산 중…")
def cached_panels(path: str, name: str, max_len: float):
    f = ifcopenshell.open(path)
    _, el, _, _ = extract_run(path, out_dir=tempfile.mkdtemp())
    return wall_panels(f, el, max_len=max_len)


@st.cache_resource(show_spinner="3D 형상 생성 중… (최초 1회, 10초 내외)")
def build_3d(path: str, name: str):
    f = ifcopenshell.open(path)
    _, el, _, _ = extract_run(path, out_dir=tempfile.mkdtemp())
    return viewer_figure(f, el)


if up is not None and not use_sample:
    schema, el, summ, sp, ifc_path = parse(up.getvalue(), up.name)
    src = up.name
elif SAMPLES[sample_key].exists():
    _p = SAMPLES[sample_key]
    schema, el, summ, sp, ifc_path = parse(_p.read_bytes(), _p.name)
    src = _p.name
else:
    st.info("IFC 파일을 업로드하세요.")
    st.stop()

_srcs = " / ".join(sorted(set(x for x in el["출처"] if x)))
st.success(f"**{src}** · {schema} · 부재 {len(el)}개 · 실 {len(sp)}개 · 물량 출처: {_srcs}")

# ── 1-1. 3D 뷰 ───────────────────────────────────────────
st.subheader("3D 뷰")
st.caption("드래그로 회전, 휠로 확대. 범례의 분류를 클릭하면 숨겨집니다(지붕을 끄면 내부가 보입니다). 부재에 마우스를 올리면 면적·체적이 표시됩니다.")
show_3d = st.toggle("3D 뷰 표시", value=True)
if show_3d:
    st.plotly_chart(build_3d(ifc_path, src), width="stretch", config={"displayModeBar": False})

# ── 2. 물량 ───────────────────────────────────────────────
st.caption("물량 출처 — BaseQuantities(IFC 표준 수량) → 기타 수량 Pset(Revit 등) → 형상 계산 순으로 폴백 · '연면적 포함'이 꺼진 실(지붕 등)은 연면적·평당가에서 제외")
c1, c2 = st.columns(2)
with c1:
    st.subheader("부재 물량 요약")
    st.dataframe(summ, hide_index=True, width="stretch")
with c2:
    st.subheader("실별 면적")
    st.dataframe(sp, hide_index=True, width="stretch")
    _inc = sp[sp["연면적 포함"]]
    st.metric("연면적(순)", f"{_inc['순면적(㎡)'].sum():.2f} ㎡  /  {_inc['평'].sum():.2f} 평",
              help="지붕 등 '연면적 포함'이 아닌 실은 제외")

with st.expander("부재 상세 (전체 목록)"):
    st.dataframe(el, hide_index=True, width="stretch")

# ── 2-1. 패널 일람표 ──────────────────────────────────────
st.subheader("벽체 패널 일람표 (패널라이징)")
st.caption("벽 1장 = 공장 제작 패널 1장으로 보고 길이·높이·개구부·순면적을 정리합니다. 운송 한계를 넘는 벽은 분할 수를 제안합니다.")
pc1, pc2 = st.columns([1, 3])
with pc1:
    max_len = st.slider("운송 한계 길이(m)", 3.0, 15.0, DEFAULT_MAX_LEN, 0.5,
                        help="트럭 적재 가능 패널 길이 가정값. 공장·차량 조건에 맞게 조정")
panels = cached_panels(ifc_path, src, max_len)
ps = panel_summary(panels)
if ps:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("벽체 수", ps["벽체 수"])
    m2.metric("패널 수(분할 후)", ps["패널 수(분할 후)"], delta=f"+{ps['패널 수(분할 후)'] - ps['벽체 수']} 분할" if ps["운송 초과 벽체"] else None, delta_color="off")
    m3.metric("운송 초과 벽체", ps["운송 초과 벽체"], help=f"최장 벽체 {ps['최장 벽체(m)']:.2f} m")
    m4.metric("외벽 / 내벽 순패널면적", f"{ps['외벽 순패널면적(㎡)']:,.1f} / {ps['내벽 순패널면적(㎡)']:,.1f} ㎡")
    st.dataframe(
        panels.drop(columns=["재료"]).style
            .format(precision=2, na_rep="–")
            .apply(lambda s: ["background-color: rgba(224,122,95,0.18)" if v else "" for v in s], subset=["운송 초과"]),
        hide_index=True, width="stretch",
    )

# ── 3. 단가표 ─────────────────────────────────────────────
st.subheader("단가표 (수정 가능)")
st.caption("단가는 가정값입니다. 셀을 더블클릭해 수정하면 견적이 즉시 재계산됩니다.")
if "prices" not in st.session_state:
    st.session_state.prices = load_prices()
prices = st.data_editor(
    st.session_state.prices,
    hide_index=True, width="stretch", num_rows="fixed",
    column_config={
        "단가(원)": st.column_config.NumberColumn(min_value=0, step=1000, format="%d"),
        "산출기준": st.column_config.TextColumn(disabled=True),
    },
    key="price_editor",
)

# ── 4. 견적 ───────────────────────────────────────────────
est = estimate(summ, sp, prices)
by, kpi = summarize_by_trade(est, sp)

st.subheader("견적")
k1, k2, k3, k4 = st.columns(4)
k1.metric("총액", f"{kpi['총액(원)']:,} 원")
k2.metric("평당", f"{kpi['평당(원)']:,} 원")
k3.metric("㎡당", f"{kpi['㎡당(원)']:,} 원")
k4.metric("연면적", f"{kpi['연면적(평)']} 평")

c3, c4 = st.columns([3, 2])
with c3:
    st.dataframe(
        est.style.format({"단가(원)": "{:,}", "금액(원)": "{:,}", "수량": "{:,.2f}"}),
        hide_index=True, width="stretch",
    )
with c4:
    fig = px.bar(by, x="공종", y="금액(원)", text="비중(%)", title="공종별 금액")
    fig.update_traces(texttemplate="%{text}%", textposition="outside")
    fig.update_layout(yaxis_tickformat=",", margin=dict(t=40, b=0))
    st.plotly_chart(fig, width="stretch")

# ── 5. 다운로드 ───────────────────────────────────────────
buf = io.BytesIO()
to_excel(buf, summ, sp, est, by, kpi, panels=panels)
st.download_button(
    "엑셀 다운로드 (요약·공종별·견적내역·부재물량·실별면적·패널일람)",
    data=buf.getvalue(),
    file_name=f"estimate_{Path(src).stem}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
